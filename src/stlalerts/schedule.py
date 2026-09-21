"""Schedule-confirmation pass: poll STL's board, watch tail assignments per flight, alert on planned
special liveries and on swaps. Independent of the ADS-B pass; both share state (dedupe + mutes).

Matching is the same as the ADS-B pass: the alertable index is NOT scoped by airline.
"""
import calendar
from datetime import datetime, timedelta

from . import state as st
from .aerodatabox import CANCELLED, fmt_local


def _iso(dt):
    return dt.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- budget / when to poll

def budget(state, now, cfg):
    """Monthly unit ledger; resets itself when the month changes."""
    adb = state["adb"]
    month = now.strftime("%Y-%m")
    if adb.get("month") != month:
        adb.clear()
        adb.update(month=month, units=0, calls=0, extras_day="", extras_today=0)
    return adb


def _latest_anchor(now, hours):
    cands = [now.replace(hour=h, minute=0, second=0, microsecond=0) - timedelta(days=d)
             for h in hours for d in (0, 1)]
    return max(c for c in cands if c <= now)


def due(state, now, cfg):
    """Return 'base', 'watch' or None (with the reason available via due_reason for logging)."""
    return _due(state, now, cfg)[0]


def _due(state, now, cfg):
    adb = budget(state, now, cfg)
    if adb.get("blocked_until") and datetime.fromisoformat(adb["blocked_until"]) > now:
        return None, f"backing off until {adb['blocked_until']}"
    cost, cap = cfg["units_per_call"], cfg["monthly_budget_units"] - cfg["budget_reserve_units"]
    if adb["units"] + cost > cap:
        return None, f"monthly budget exhausted ({adb['units']}/{cfg['monthly_budget_units']} units)"
    last = datetime.fromisoformat(adb["last_poll"]) if adb.get("last_poll") else None

    if last is None or last < _latest_anchor(now, cfg["base_poll_hours_utc"]):
        return "base", "scheduled base poll"

    # Extra polls: only while a flight we already know is special is about to operate (catch swaps).
    if last <= now - timedelta(minutes=cfg["watch_interval_minutes"]):
        soon = [e for e in state["flights"].values() if e.get("livery") and _within(e, now, cfg)]
        if soon:
            today = now.strftime("%Y-%m-%d")
            if adb.get("extras_day") != today:
                adb["extras_day"], adb["extras_today"] = today, 0
            if adb["extras_today"] >= cfg["max_extra_calls_per_day"]:
                return None, "extra-poll cap for today reached"
            days_left = calendar.monthrange(now.year, now.month)[1] - now.day + 1
            base_reserve = len(cfg["base_poll_hours_utc"]) * days_left * cost
            if adb["units"] + cost + base_reserve > cap:
                return None, "extra poll would eat into the base-poll reserve"
            return "watch", "special-livery flight coming up"
    return None, "nothing due"


def _within(entry, now, cfg):
    t = datetime.fromisoformat(entry["sched_utc"])
    return now - timedelta(minutes=10) <= t <= now + timedelta(hours=cfg["watch_horizon_hours"])


def record_poll(state, now, cfg, reason, headers=None):
    adb = budget(state, now, cfg)
    adb["units"] += cfg["units_per_call"]
    adb["calls"] += 1
    adb["last_poll"] = _iso(now)
    adb.pop("blocked_until", None)
    if reason == "watch":
        adb["extras_today"] = adb.get("extras_today", 0) + 1
    if headers:
        adb["last_headers"] = headers


def block(state, now, hours):
    budget(state, now, None)  # make sure the ledger is for this month before writing into it
    state["adb"]["blocked_until"] = _iso(now + timedelta(hours=hours))


# --------------------------------------------------------------------------- tail tracking + alerts

def prune(state, now, cfg):
    keep = now - timedelta(hours=cfg["flight_retention_hours"])
    state["flights"] = {k: e for k, e in state["flights"].items()
                        if datetime.fromisoformat(e["sched_utc"]) > keep}


def _tag(flight, watch_codes):
    return "routine" if flight["airline_icao"] in watch_codes else "diversion?"


def _alert(kind, f, row, watch_codes, **extra):
    base = {
        "alert_type": kind, "flight_number": f["number"], "direction": f["direction"],
        "scheduled": fmt_local(f["local_time"]), "gate": f["gate"], "terminal": f["terminal"],
        "other_airport": f["other_airport"], "tag": _tag(f, watch_codes),
        "airline": f["airline"] or (row or {}).get("airline", ""),  # the board's operator, not the DB's
        "aircraft_type": (row or {}).get("aircraft_type") or f["aircraft_model"],
        "status": (row or {}).get("status", "active"), "confidence": (row or {}).get("confidence", "verified"),
        "hex": f["mode_s"],
    }
    base.update(extra)
    return base


def process(flights, state, by_reg, by_hex, watch_codes, now, cfg, lead_rows):
    """Update per-flight tail tracking and return the alerts to send.

    Each returned alert carries `_key` and `_undo` so a failed send can be rolled back and retried next poll.
    """
    alerts = []
    for f in flights:
        if f["status"].lower() in CANCELLED or not f["flight_key"] or not f["best_utc"]:
            continue
        if f["best_utc"] < now - timedelta(minutes=15):
            continue
        key = f"{f['flight_key']}|{f['local_date']}|{f['direction'][0]}"
        ent = state["flights"].get(key)
        if ent is None:
            ent = state["flights"][key] = {
                "reg": None, "livery": None, "alerted_reg": None, "first_seen": _iso(now),
                "had_reg_at_first_sight": bool(f["reg"]),
            }
        ent["sched_utc"] = _iso(f["best_utc"])
        reg = f["reg"]
        if not reg:
            continue  # no tail yet: normal, try again next poll
        row = by_reg.get(reg) or (by_hex.get(f["mode_s"]) if f["mode_s"] else None)
        livery = row["livery_name"] if row else None
        old = (ent["reg"], ent["livery"], ent["alerted_reg"])
        old_reg, old_livery = ent["reg"], ent["livery"]
        if reg == old_reg:
            continue  # unchanged tail: never alert twice

        ent["reg"], ent["livery"] = reg, livery
        if old_reg is None:
            lead_rows.append({
                "flight_key": f["flight_key"], "direction": f["direction"], "sched_utc": ent["sched_utc"],
                "first_seen_utc": ent["first_seen"], "tail_seen_utc": _iso(now),
                "hours_before": round((f["best_utc"] - now).total_seconds() / 3600, 2),
                "seen_without_tail_first": not ent["had_reg_at_first_sight"],
            })
            kind = "planned" if row else None
        elif old_livery and livery:
            kind = "swap_change"      # special -> a different special tail: one combined alert
        elif old_livery:
            kind = "swap_out"
        elif livery:
            kind = "swap_in"
        else:
            kind = None
        if kind is None:
            continue

        subject = old_reg if kind == "swap_out" else reg
        if st.is_muted(state, subject, now):
            continue
        if kind != "swap_out" and st.live_alerted(state, reg, f["flight_key"]):
            ent["alerted_reg"] = reg   # the ADS-B pass already told you about this tail on this flight
            continue
        a = _alert(kind, f, row, watch_codes, registration=subject, livery_name=livery or old_livery,
                   old_reg=old_reg, old_livery=old_livery, new_reg=reg, new_livery=livery)
        if kind != "swap_out":
            ent["alerted_reg"] = reg
        elif ent["alerted_reg"] == old_reg:
            ent["alerted_reg"] = None
        a["_key"], a["_undo"] = key, old
        alerts.append(a)
    return alerts


def undo(state, alert):
    """Roll back tracking for an alert whose send failed, so the next poll re-detects and retries it."""
    ent = state["flights"].get(alert["_key"])
    if ent:
        ent["reg"], ent["livery"], ent["alerted_reg"] = alert["_undo"]
