"""Make ONE real AeroDataBox call for STL and report what actually comes back.

  ADB_KEY=... [ADB_PROVIDER=rapidapi|apimarket|custom] python scripts/probe_aerodatabox.py [--save fixture.json]

This SPENDS API units (probably 2). Use it to confirm: the endpoint works with your key, what fraction of
flights already have a tail, whether gate/terminal are populated, and what the rate-limit headers say about cost.
(PowerShell: $env:ADB_KEY="..."; py scripts/probe_aerodatabox.py)
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stlalerts import aerodatabox, liveries  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", help="write the raw response here (handy as a test fixture)")
    ap.add_argument("--hours", type=int, default=12)
    a = ap.parse_args()
    cfg = json.loads((ROOT / "config" / "config.json").read_text())
    key = os.environ.get("ADB_KEY")
    if not key:
        sys.exit("Set ADB_KEY first.")
    provider = os.environ.get("ADB_PROVIDER") or cfg["schedule"]["provider"]
    client = aerodatabox.Client(key, provider)
    try:
        data, headers = client.fids(cfg["schedule"]["airport_icao"], -30, a.hours * 60, cfg["schedule"]["include_cargo"])
    except aerodatabox.AeroDataBoxError as e:
        sys.exit(f"API error: {e}\n(check provider={provider!r}, the key, and that you are subscribed to the plan)")
    if a.save:
        Path(a.save).write_text(json.dumps(data, indent=1))
        print("raw response saved to", a.save)

    flights = aerodatabox.parse_fids(data)
    print(f"\nprovider={provider}  window={a.hours}h  flights={len(flights)}")
    print("rate-limit/unit headers:", headers or "(none exposed)")
    for d in ("Departure", "Arrival"):
        fs = [f for f in flights if f["direction"] == d]
        print(f"{d}s: {len(fs)}, with tail: {sum(1 for f in fs if f['reg'])}, "
              f"with gate: {sum(1 for f in fs if f['gate'])}, with terminal: {sum(1 for f in fs if f['terminal'])}")
    tails = [f for f in flights if f["reg"]]
    by_airline = collections.Counter((f["airline"], bool(f["reg"])) for f in flights)
    print("\nby airline (airline, has_tail): count")
    for (name, has), n in by_airline.most_common(12):
        print(f"  {name or '?'} tail={has}: {n}")
    if tails:
        soonest = min(f["best_utc"] for f in tails if f["best_utc"])
        latest = max(f["best_utc"] for f in tails if f["best_utc"])
        print(f"\ntails present for flights scheduled between {soonest:%H:%MZ} and {latest:%H:%MZ}")
    rows = liveries.load(ROOT / "data" / "liveries.csv")
    special = {r["registration"]: r for r in rows if r["status"] != "retired"}
    print("\nspecial-livery matches on this board (any airline, any kind):")
    hits = [f for f in tails if f["reg"] in special]
    for f in hits:
        r = special[f["reg"]]
        print(f"  {f['number']:8} {f['direction']:9} {f['local_time']:22} {f['reg']:8} {r['livery_name']} [{r['kind']}]")
    if not hits:
        print("  none")
    print("\nfirst 5 raw-parsed flights:")
    for f in flights[:5]:
        print(" ", {k: (str(v) if v is not None else None) for k, v in f.items() if k not in ('mode_s',)})


if __name__ == "__main__":
    main()
