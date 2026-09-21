"""Livery database: a plain CSV, one row per tail number."""
import csv
from pathlib import Path

FIELDS = [
    "registration", "icao24", "airline", "aircraft_type", "livery_name", "kind",
    "description", "status", "confidence", "painted_date", "as_of", "sources", "notes", "country",
]

# status:     active | unverified | retired   (retired rows never alert)
# kind:       livery | retro | decal | partnership | alliance | other
# confidence: verified (hand-checked) | best-effort (single/unchecked source)


def normalize_reg(reg):
    return (reg or "").strip().upper().replace(" ", "")


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    for r in rows:
        for k in FIELDS:
            r.setdefault(k, "")
        r["registration"] = normalize_reg(r["registration"])
        r["icao24"] = r["icao24"].lower()
    return rows


def save(path, rows):
    rows = sorted(rows, key=lambda r: (r["airline"].lower(), r["registration"]))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


CONFLICT_FIELDS = ["registration", "field", "kept", "source_says", "source"]


def write_conflicts(path, new, replace_sources):
    """Rewrite the conflicts report, replacing only rows from the given sources (others are kept)."""
    keep = []
    if Path(path).exists():
        with open(path, newline="", encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if r["source"] not in replace_sources]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CONFLICT_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(keep + new)


def alertable(rows, statuses, kinds):
    """Index of rows eligible to alert. NOT filtered by airline (diversions must match)."""
    by_reg, by_hex = {}, {}
    for r in rows:
        if r["status"] not in statuses or r["kind"] not in kinds:
            continue
        by_reg[r["registration"]] = r
        if r["icao24"]:
            by_hex[r["icao24"]] = r
    return by_reg, by_hex
