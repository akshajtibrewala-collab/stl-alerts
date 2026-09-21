"""Persistent state (dedupe + mutes), stored as JSON and committed by the workflow."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .flights import callsign_key


def now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def load(path):
    p = Path(path)
    data = json.loads(p.read_text()) if p.exists() else {}
    data.setdefault("alerted", {})   # "REG|CALLSIGN" -> iso time of last alert
    data.setdefault("mutes", {})     # "REG" -> iso expiry
    data.setdefault("ctl_since", "24h")
    data.setdefault("flights", {})   # schedule pass: "SWA283|2026-09-21|D" -> tail/livery tracking
    data.setdefault("adb", {})       # schedule pass: monthly unit budget + last poll
    data.setdefault("keepalive", _iso(now()))
    return data


def save(path, state):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def prune(state, cooldown_hours, at=None):
    at = at or now()
    keep = at - timedelta(hours=cooldown_hours)
    state["alerted"] = {k: v for k, v in state["alerted"].items()
                        if datetime.fromisoformat(v) > keep}
    state["mutes"] = {k: v for k, v in state["mutes"].items()
                      if datetime.fromisoformat(v) > at}


def is_muted(state, reg, at=None):
    exp = state["mutes"].get(reg)
    return bool(exp) and datetime.fromisoformat(exp) > (at or now())


def mute(state, reg, hours, at=None):
    state["mutes"][reg] = _iso((at or now()) + timedelta(hours=hours))


def planned_covers(state, reg, callsign, at=None):
    """True if the schedule pass already alerted about this tail on the flight this callsign belongs to."""
    fk = callsign_key(callsign)
    if not fk:
        return False
    at = at or now()
    for k, e in state["flights"].items():
        fresh = datetime.fromisoformat(e["sched_utc"]) > at - timedelta(hours=6)
        if e.get("alerted_reg") == reg and k.startswith(fk + "|") and fresh:
            return True
    return False


def live_alerted(state, reg, fkey):
    """True if the ADS-B pass already alerted (within cooldown) about this tail on this flight."""
    for k in state["alerted"]:
        r, _, cs = k.partition("|")
        if r == reg and callsign_key(cs) == fkey:
            return True
    return False


def alert_key(match):
    return f"{match['registration']}|{match['callsign'] or '-'}"


def should_alert(state, match, cooldown_hours, at=None):
    at = at or now()
    if is_muted(state, match["registration"], at):
        return False
    last = state["alerted"].get(alert_key(match))
    return not (last and datetime.fromisoformat(last) > at - timedelta(hours=cooldown_hours))


def record(state, match, at=None):
    state["alerted"][alert_key(match)] = _iso(at or now())
