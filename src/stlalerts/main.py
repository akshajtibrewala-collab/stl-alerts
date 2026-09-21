"""One polling run: ADS-B pass + schedule pass, delivered through one dedupe/mute path."""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import aerodatabox, liveries, notify, schedule
from . import state as st
from .adsb import AdsbClient, RateLimited
from .match import find_matches

ROOT = Path(__file__).resolve().parents[2]
LEAD_CSV = ROOT / "state" / "lead_times.csv"
LEAD_FIELDS = ["flight_key", "direction", "sched_utc", "first_seen_utc", "tail_seen_utc",
               "hours_before", "seen_without_tail_first"]


def adsb_pass(cfg, args, by_reg, by_hex):
    if args.fixture:
        aircraft = json.loads(Path(args.fixture).read_text()).get("ac", [])
    else:
        client = AdsbClient(cfg["adsb"]["base_url"], cfg["adsb"]["min_interval_seconds"],
                            api_key=os.environ.get("ADSB_API_KEY"))
        try:
            aircraft = client.point(cfg["airport"]["lat"], cfg["airport"]["lon"],
                                    min(cfg["geometry"]["inbound_radius_nm"], 250))
        except RateLimited:
            print("adsb.lol rate-limited this cycle; skipping", file=sys.stderr)
            aircraft = []
    print(f"adsb: {len(aircraft)} aircraft in range")
    matches = find_matches(aircraft, by_reg, by_hex, cfg["airport"], cfg["geometry"], cfg["watch_airlines"])
    matches = [m for m in matches if m["phase"] in cfg["alerts"]["phases"]]
    matches.sort(key=lambda m: (m["tag"] != "routine", m["dist_nm"]))  # presentation order only
    return matches


def schedule_pass(cfg, args, state, by_reg, by_hex, now):
    """Returns alerts. Never raises: a failure here must not disturb the ADS-B pass."""
    scfg = cfg["schedule"]
    if not scfg["enabled"]:
        return []
    try:
        if args.schedule_fixture:
            data, headers, reason = json.loads(Path(args.schedule_fixture).read_text()), {}, "fixture"
        else:
            key = os.environ.get("ADB_KEY")
            if not key:
                print("schedule: ADB_KEY not set; pass skipped")
                return []
            reason, why = schedule._due(state, now, scfg)
            if args.force_schedule and not reason:
                reason = "base"
            if not reason:
                print(f"schedule: not due ({why})")
                return []
            if args.dry_run and not args.force_schedule:
                print(f"schedule: {reason} poll is due, but --dry-run doesn't spend API units "
                      "(use --schedule-fixture, or --force-schedule to really call it)")
                return []
            client = aerodatabox.Client(key, os.environ.get("ADB_PROVIDER") or scfg["provider"])
            try:
                data, headers = client.fids(scfg["airport_icao"], -30, scfg["window_hours"] * 60,
                                            scfg["include_cargo"])
            except aerodatabox.AeroDataBoxError as e:
                hours = 2 if e.status == 429 else 3
                schedule.block(state, now, hours)
                print(f"schedule: API error ({e}); backing off {hours}h, ADS-B pass unaffected", file=sys.stderr)
                return []
            except OSError as e:
                schedule.block(state, now, 0.5)
                print(f"schedule: network error ({e}); retrying in 30 min", file=sys.stderr)
                return []
            schedule.record_poll(state, now, scfg, reason, headers)
        flights = aerodatabox.parse_fids(data)
        with_tail = sum(1 for f in flights if f["reg"])
        print(f"schedule ({reason}): {len(flights)} flights, {with_tail} with a tail assigned; "
              f"units used this month: {state['adb'].get('units', 0)}/{scfg['monthly_budget_units']}")
        schedule.prune(state, now, scfg)
        lead_rows = []
        alerts = schedule.process(flights, state, by_reg, by_hex, set(cfg["watch_airline_codes"]),
                                  now, scfg, lead_rows)
        if lead_rows and not args.dry_run:
            new = not LEAD_CSV.exists()
            with open(LEAD_CSV, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=LEAD_FIELDS, lineterminator="\n")
                if new:
                    w.writeheader()
                w.writerows(lead_rows)
        return alerts
    except Exception as e:  # noqa: BLE001 - isolation is the point
        print(f"schedule: unexpected error, skipped this run: {e!r}", file=sys.stderr)
        return []


def run(args):
    cfg = json.loads((ROOT / "config" / "config.json").read_text())
    acfg = cfg["alerts"]
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    topic = os.environ.get("NTFY_TOPIC", "")
    state_path = Path(args.state_file) if args.state_file else ROOT / "state" / "state.json"
    state = st.load(state_path)
    now = st.now()
    ctl_url = f"{server}/{topic}-ctl"

    if args.test_notify:
        live = {"registration": "N492AS", "hex": "", "callsign": "ASA388", "airline": "Alaska Airlines",
                "livery_name": "TEST live alert", "aircraft_type": "B739", "phase": "inbound",
                "dist_nm": 18, "alt_ft": 4200, "tag": "routine", "status": "active", "confidence": "verified"}
        plan = {"alert_type": "planned", "registration": "N492AS", "livery_name": "TEST planned alert",
                "airline": "Alaska", "flight_number": "AS 388", "direction": "Arrival", "other_airport": "SEA",
                "scheduled": "Mon 2:10 PM", "gate": "", "terminal": "1", "tag": "routine", "status": "unverified",
                "confidence": "best-effort", "old_reg": None, "new_reg": "N492AS", "old_livery": None, "new_livery": None}
        swap = {**plan, "alert_type": "swap_change", "livery_name": "TEST swap", "old_reg": "N492AS",
                "new_reg": "N500WR", "old_livery": "TEST old", "new_livery": "TEST new", "direction": "Departure",
                "other_airport": "MDW", "status": "active"}
        for msg in (notify.build_message(live, acfg["mute_hours"], ctl_url),
                    notify.build_schedule_message(plan, acfg["mute_hours"], ctl_url),
                    notify.build_schedule_message(swap, acfg["mute_hours"], ctl_url)):
            print(msg["title"], "|", msg["body"].replace("\n", " / "), "| click ->", msg["click"])
            if topic:
                print("sent:", notify.send(server, topic, msg))
        return 0

    if topic and not args.dry_run:
        try:
            for line in notify.apply_control_messages(state, server, f"{topic}-ctl"):
                print("control:", line)
        except Exception as e:  # control channel must never block alerting
            print("control channel error:", e, file=sys.stderr)

    rows = liveries.load(ROOT / "data" / "liveries.csv")
    by_reg, by_hex = liveries.alertable(rows, set(acfg["statuses"]), set(acfg["kinds"]))
    print(f"livery DB: {len(rows)} rows, {len(by_reg)} alertable")
    st.prune(state, acfg["cooldown_hours"])

    # Two independent passes; each is fenced so one failing never blocks the other.
    try:
        live = adsb_pass(cfg, args, by_reg, by_hex)
    except Exception as e:  # noqa: BLE001
        print(f"adsb: unexpected error, skipped this run: {e!r}", file=sys.stderr)
        live = []
    planned = schedule_pass(cfg, args, state, by_reg, by_hex, now)

    def deliver(msg, on_ok, on_fail=None):
        print(f"ALERT: {msg['title']} | {msg['body']!r}")
        if args.dry_run or not topic:
            if not topic and not args.dry_run:
                print("NTFY_TOPIC not set; not sending", file=sys.stderr)
            if on_fail:
                on_fail()  # nothing was sent, so don't let tracking claim it was
            return
        try:
            notify.send(server, topic, msg)
            on_ok()
        except Exception as e:  # noqa: BLE001
            print("ntfy send failed:", e, file=sys.stderr)
            if on_fail:
                on_fail()

    for a in planned:
        msg = notify.build_schedule_message(a, acfg["mute_hours"], ctl_url)
        deliver(msg, on_ok=lambda: None, on_fail=lambda a=a: schedule.undo(state, a))

    for m in live:
        if not st.should_alert(state, m, acfg["cooldown_hours"]):
            print(f"suppressed (muted/duplicate): {m['registration']} {m['callsign']}")
            continue
        deliver(notify.build_message(m, acfg["mute_hours"], ctl_url), on_ok=lambda m=m: st.record(state, m))

    # Touch state monthly so the repo shows activity (GitHub pauses idle cron workflows).
    if datetime.fromisoformat(state["keepalive"]) < now - timedelta(days=25):
        state["keepalive"] = now.isoformat(timespec="seconds")
    if not args.dry_run:
        st.save(state_path, state)
    return 0


def cli():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="print alerts, send nothing, save nothing")
    p.add_argument("--fixture", help="adsb.lol-shaped JSON instead of a live ADS-B call")
    p.add_argument("--schedule-fixture", help="AeroDataBox FIDS-shaped JSON instead of a live (unit-spending) call")
    p.add_argument("--force-schedule", action="store_true", help="poll AeroDataBox now even if not due (spends units)")
    p.add_argument("--state-file", help="use this state JSON instead of state/state.json (tests)")
    p.add_argument("--test-notify", action="store_true", help="send one fake live + one fake planned alert (real tail, so the tap link opens)")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    cli()
