"""One-shot import of https://airportwebcams.net/special-liveries/ into data/liveries.csv.

  python scripts/import_airportwebcams.py                 # fetch (one plain GET) and import
  python scripts/import_airportwebcams.py --offline f.html

Adds new tails only. Any disagreement with a row we already have (different livery name, or we have the
tail as retired) is logged to data/conflicts.csv and the existing row is left untouched. Appends a summary
to data/import_log.md. Safe to re-run: existing tails are skipped, new ones are added.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stlalerts import airportwebcams as awc, liveries  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", help="use a saved copy of the page instead of fetching")
    a = ap.parse_args()
    page = Path(a.offline).read_text(encoding="utf-8", errors="replace") if a.offline else awc.fetch()
    incoming, skipped = awc.parse(page)
    updated = awc.site_updated(page)

    path = ROOT / "data" / "liveries.csv"
    rows = liveries.load(path)
    before = len(rows)
    today = date.today().isoformat()
    added, agreed, conflicts = awc.merge(rows, incoming, today)
    liveries.save(path, rows)
    liveries.write_conflicts(ROOT / "data" / "conflicts.csv", conflicts, {awc.SOURCE})

    site_regs = {r["registration"] for r in incoming}
    absent = [r["registration"] for r in rows
              if r["status"] in ("active", "unverified") and awc.SOURCE not in r["sources"]
              and r["registration"] not in site_regs]
    kinds = {}
    for r in incoming:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    log = ROOT / "data" / "import_log.md"
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n## {today}: airportwebcams.net import\n"
                f"- Site self-reported last update: **{updated or 'unknown'}** (imported {today}).\n"
                f"- Table rows parsed: {len(incoming) + len(skipped)}; imported/considered: {len(incoming)}; "
                f"skipped: {len(skipped)} ({', '.join(sorted({s[0] for s in skipped})) or 'none'}).\n"
                f"- New tails added: {added}. Already present and consistent: {agreed}. "
                f"Conflicts logged (existing row kept): {len(conflicts)}.\n"
                f"- Kinds in this import: {kinds}.\n"
                f"- Our active/unverified rows NOT on the site snapshot (possibly stale): {len(absent)}.\n")
    print(f"site updated {updated}; parsed {len(incoming)} usable rows, skipped {len(skipped)}")
    for reason, cells in skipped:
        print("  skipped:", reason, cells)
    print(f"added {added}, consistent {agreed}, conflicts {len(conflicts)}; DB {before} -> {len(rows)} rows")
    print("kinds:", kinds)
    print("our active/unverified rows absent from site:", len(absent), absent[:40])


if __name__ == "__main__":
    main()
