"""Parse Wikipedia fleet pages into livery rows.

Only Southwest's page has a structured special-livery table with tail numbers.
Other airlines' pages mention tails in prose, so they are maintained by hand
(or add a parser here when a page grows a table).
"""
import re
import urllib.parse
import urllib.request

RAW_URL = "https://en.wikipedia.org/w/index.php?title={title}&action=raw"
UA = "stl-livery-alerts/0.1 (personal hobby project)"
N_REG = re.compile(r"\b(N[1-9][0-9A-Z]{0,4})\b")


def fetch_raw(title):
    req = urllib.request.Request(RAW_URL.format(title=urllib.parse.quote(title)), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def _clean(text):
    text = re.sub(r"<ref[^>]*?/>", "", text)
    text = re.sub(r"<ref.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    text = text.replace("''", "").replace("<br />", " ").replace("<br/>", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_southwest(wikitext, airline="Southwest Airlines"):
    start = wikitext.index("current and former special liveries")
    table = wikitext[start: wikitext.index("\n|}", start)]
    section = "Active"
    rows = []
    for chunk in table.split("\n|-"):
        header = re.search(r"^!\s*colspan[^|]*\|\s*(.+)$", chunk, flags=re.M)
        if header:
            section = _clean(header.group(1))
            continue
        cells = [l[1:] for l in chunk.split("\n") if l.startswith("|") and not l.startswith(("|+", "|}"))]
        if len(cells) < 4:
            continue
        name, year, desc, regcell = _clean(cells[0]), _clean(cells[1]), _clean(cells[2]), cells[3]
        if re.search(r'original "Desert Gold"|Retro', name + desc):
            kind = "retro"
        elif "decal" in desc and "flag of" not in desc:
            kind = "decal"
        else:
            kind = "livery"
        for part in re.split(r"<br\s*/?>", regcell):
            m = N_REG.search(part)
            if not m:
                continue
            tag = re.search(r"\((current|previous)\)", part)
            retired = section.lower().startswith(("former", "retired")) or (tag and tag.group(1) == "previous")
            rows.append({
                "registration": m.group(1), "icao24": "", "airline": airline, "aircraft_type": "",
                "livery_name": name, "kind": kind, "description": desc[:200],
                "status": "retired" if retired else "active", "confidence": "best-effort",
                "painted_date": "", "as_of": "", "sources": "wikipedia:Southwest_Airlines_fleet",
                "notes": f"livery first introduced {year} (not this tail's paint date)",
            })
    return rows
