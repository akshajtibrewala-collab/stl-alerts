"""One polling run: fetch ADS-B near STL, match against the livery DB, alert."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import liveries, notify
from . import state as st
from .adsb import AdsbClient, RateLimited
from .match import find_matches

ROOT = Path(__file__).resolve().parents[2]


def run(args):
    cfg = json.loads((ROOT / "config" / "config.json").read_text())
    acfg, gcfg = cfg["alerts"], cfg["geometry"]
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    topic = os.environ.get("NTFY_TOPIC", "")
    state_path = ROOT / "state" / "state.json"
    state = st.load(state_path)

    if args.test_notify:
        m = {"registration": "N000TEST", "hex": "", "callsign": "TEST1", "airline": "Test Air",
             "livery_name": "Test Livery", "aircraft_type": "B738", "phase": "arrival",
             "dist_nm": 12, "alt_ft": 3400, "tag": "routine", "status": "active",
             "confidence": "verified"}
        msg = notify.build_message(m, acfg["mute_hours"], f"{server}/{topic}-ctl")
        print(msg)
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

    if args.fixture:
        aircraft = json.loads(Path(args.fixture).read_text()).get("ac", [])
    else:
        client = AdsbClient(cfg["adsb"]["base_url"], cfg["adsb"]["min_interval_seconds"],
                            api_key=os.environ.get("ADSB_API_KEY"))
        try:
            aircraft = client.point(cfg["airport"]["lat"], cfg["airport"]["lon"],
                                    min(gcfg["inbound_radius_nm"], 250))
        except RateLimited:
            print("adsb.lol rate-limited this cycle; skipping", file=sys.stderr)
            aircraft = []
    print(f"aircraft in range: {len(aircraft)}")

    st.prune(state, acfg["cooldown_hours"])
    matches = find_matches(aircraft, by_reg, by_hex, cfg["airport"], gcfg, cfg["watch_airlines"])
    matches = [m for m in matches if m["phase"] in acfg["phases"]]
    matches.sort(key=lambda m: (m["tag"] != "routine", m["dist_nm"]))  # presentation order only

    ctl_url = f"{server}/{topic}-ctl"
    for m in matches:
        if not st.should_alert(state, m, acfg["cooldown_hours"]):
            print(f"suppressed (muted/duplicate): {m['registration']} {m['callsign']}")
            continue
        msg = notify.build_message(m, acfg["mute_hours"], ctl_url)
        print(f"ALERT: {msg['title']} | {msg['body']!r}")
        if args.dry_run or not topic:
            if not topic and not args.dry_run:
                print("NTFY_TOPIC not set; not sending", file=sys.stderr)
            continue
        try:
            notify.send(server, topic, msg)
            st.record(state, m)  # only after a successful send, so failures retry
        except Exception as e:
            print("ntfy send failed:", e, file=sys.stderr)

    # Touch state monthly so the repo shows activity (GitHub pauses idle cron workflows).
    if datetime.fromisoformat(state["keepalive"]) < st.now() - timedelta(days=25):
        state["keepalive"] = st.now().isoformat(timespec="seconds")
    if not args.dry_run:
        st.save(state_path, state)
    return 0


def cli():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="print alerts, send nothing, save nothing")
    p.add_argument("--fixture", help="JSON file shaped like an adsb.lol response instead of a live call")
    p.add_argument("--test-notify", action="store_true", help="send one fake alert to verify the phone")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    cli()
