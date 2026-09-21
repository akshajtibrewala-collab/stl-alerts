"""Bulk-refresh data/liveries.csv from scrapeable sources. Safe to re-run.

  python scripts/refresh_liveries.py            # fetch and merge
  python scripts/refresh_liveries.py --offline path/to/wikitext.txt

Rules:
  * Rows marked confidence=verified, or whose sources mention "manual", are NEVER
    overwritten. If a source disagrees with one, it is logged to data/conflicts.csv.
  * Other rows are updated in place; new tails are appended as best-effort.
  * Nothing is ever deleted: a tail that vanishes from a source is left alone.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stlalerts import liveries, wikipedia  # noqa: E402

SOURCES = [
    {"title": "Southwest_Airlines_fleet", "parse": wikipedia.parse_southwest},
]
COMPARE = ("livery_name", "status")


def protected(row):
    return row["confidence"] == "verified" or "manual" in row["sources"]


def merge(existing, candidates, today):
    by_reg = {r["registration"]: r for r in existing}
    conflicts, added, updated = [], 0, 0
    for c in candidates:
        c["as_of"] = today
        cur = by_reg.get(c["registration"])
        if cur is None:
            existing.append(c)
            by_reg[c["registration"]] = c
            added += 1
            continue
        diffs = [f for f in COMPARE if cur[f] != c[f]]
        if not diffs:
            if c["sources"] not in cur["sources"]:
                cur["sources"] = (cur["sources"] + ";" + c["sources"]).strip(";")
            continue
        if protected(cur):
            for f in diffs:
                conflicts.append({"registration": c["registration"], "field": f,
                                  "kept": cur[f], "source_says": c[f], "source": c["sources"]})
        else:
            for f in COMPARE + ("description", "painted_date", "kind"):
                cur[f] = c[f]
            cur["as_of"] = today
            updated += 1
    return added, updated, conflicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", help="use this saved Southwest wikitext instead of fetching")
    args = ap.parse_args()
    path = ROOT / "data" / "liveries.csv"
    rows = liveries.load(path)
    candidates = []
    for src in SOURCES:
        text = Path(args.offline).read_text(encoding="utf-8") if args.offline else wikipedia.fetch_raw(src["title"])
        got = src["parse"](text)
        print(f"{src['title']}: parsed {len(got)} rows")
        candidates += got
    added, updated, conflicts = merge(rows, candidates, date.today().isoformat())
    liveries.save(path, rows)
    liveries.write_conflicts(ROOT / "data" / "conflicts.csv", conflicts, {c["sources"] for c in candidates})
    print(f"added {added}, updated {updated}, conflicts {len(conflicts)} (see data/conflicts.csv)")


if __name__ == "__main__":
    main()
