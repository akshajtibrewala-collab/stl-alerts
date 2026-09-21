"""Add or update one tail in seconds.

  python scripts/add_livery.py N8977G "Southwest Airlines" "Louisiana One" --type B738 --url https://...
  python scripts/add_livery.py N123XX "Some Air" "Retro" --status retired

Rows added here are marked confidence=verified + source "manual", so refresh_liveries.py
will never overwrite them. (You can also just edit data/liveries.csv in a spreadsheet.)
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stlalerts import liveries  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("registration")
    ap.add_argument("airline")
    ap.add_argument("livery_name")
    ap.add_argument("--type", default="")
    ap.add_argument("--kind", default="livery", choices=["livery", "retro", "decal", "partnership", "alliance", "other"])
    ap.add_argument("--status", default="active", choices=["active", "unverified", "retired"])
    ap.add_argument("--painted", default="")
    ap.add_argument("--url", default="", help="source link (photo/press release)")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()

    path = ROOT / "data" / "liveries.csv"
    rows = liveries.load(path)
    reg = liveries.normalize_reg(a.registration)
    new = {"registration": reg, "icao24": "", "airline": a.airline, "aircraft_type": a.type,
           "livery_name": a.livery_name, "kind": a.kind, "description": "", "status": a.status,
           "confidence": "verified", "painted_date": a.painted, "as_of": date.today().isoformat(),
           "sources": ("manual;" + a.url).strip(";"), "notes": a.notes}
    existing = next((r for r in rows if r["registration"] == reg), None)
    if existing:
        existing.update(new)
        print("updated", reg)
    else:
        rows.append(new)
        print("added", reg)
    liveries.save(path, rows)


if __name__ == "__main__":
    main()
