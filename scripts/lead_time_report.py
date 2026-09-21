"""Summarise how far ahead tails are actually assigned, from state/lead_times.csv (written by the schedule pass).

  python scripts/lead_time_report.py

'hours_before' is how long before the scheduled time we first SAW a tail on a flight. Rows where the flight
was already showing a tail the first time we saw it (seen_without_tail_first=False) are only a lower bound
on the real lead time, so they're reported separately.
"""
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def main():
    path = ROOT / "state" / "lead_times.csv"
    if not path.exists():
        sys.exit("No state/lead_times.csv yet: let the workflow run for a few days first (git pull to fetch it).")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    print(f"{len(rows)} tail assignments observed")
    for label, subset in (
        ("first seen WITHOUT a tail, then assigned (accurate to poll spacing)",
         [r for r in rows if r["seen_without_tail_first"] == "True"]),
        ("already had a tail when first seen (lower bound only)",
         [r for r in rows if r["seen_without_tail_first"] != "True"]),
    ):
        hrs = [float(r["hours_before"]) for r in subset]
        if not hrs:
            print(f"\n{label}: none yet")
            continue
        print(f"\n{label}: n={len(hrs)}")
        print(f"  hours before scheduled time: min {min(hrs):.1f}, p25 {pct(hrs, 25):.1f}, "
              f"median {statistics.median(hrs):.1f}, p75 {pct(hrs, 75):.1f}, max {max(hrs):.1f}")


if __name__ == "__main__":
    main()
