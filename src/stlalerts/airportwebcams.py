"""Import the airportwebcams.net special-liveries table (a TablePress table fully embedded in the page HTML).

robots.txt allows this path (it only blocks /wp-admin/ etc. for generic bots) and the site publishes no
terms restricting automated access, so this does ONE plain GET of the public page. No internal API is called.
"""
import html
import re
import urllib.request

URL = "https://airportwebcams.net/special-liveries/"
UA = "stl-livery-alerts/0.1 (personal hobby project; single page fetch)"
SOURCE = "airportwebcams.net"

ALLIANCE = re.compile(r"star alliance|oneworld|sky\s?team", re.I)
RETRO = re.compile(r"retro|heritage|throwback|classic colou?rs", re.I)
NEW_TAG = re.compile(r"\s*\(#NEW[^)]*\)", re.I)


def fetch(url=URL):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def site_updated(page):
    m = re.search(r"Last Updated:\s*([0-9]{1,2}-[A-Za-z]+-[0-9]{4})", page)
    return m.group(1) if m else ""


def classify_kind(desc):
    if ALLIANCE.search(desc):
        return "alliance"
    if re.search(r"\bsticker\b", desc, re.I):
        return "decal"
    if RETRO.search(desc):
        return "retro"
    return "livery"


def parse(page):
    """Return (rows, skipped). rows are livery-CSV dicts; skipped is [(reason, cells)]."""
    start = page.index('<table id="tablepress-')
    table = page[start: page.index("</table>", start)]
    updated = site_updated(page)
    rows, skipped, seen = [], [], set()
    for tr in re.findall(r'<tr class="row-\d+">(.*?)</tr>', table, flags=re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)]
        if len(cells) != 5:
            continue
        country, airline, typ, reg, desc = cells
        reg = reg.upper().replace(" ", "")
        if reg in ("VARIOUS", "N/A", "TBC") or not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,11}", reg):
            skipped.append(("not a registration", cells))
            continue
        if reg in seen:
            skipped.append(("duplicate on site", cells))
            continue
        seen.add(reg)
        notes = [f"site updated {updated}"] if updated else []
        if NEW_TAG.search(desc):
            notes.append("flagged NEW by site")
            desc = NEW_TAG.sub("", desc).strip()
        rows.append({
            "registration": reg, "icao24": "", "airline": airline, "aircraft_type": typ,
            "livery_name": desc, "kind": classify_kind(desc), "description": "", "status": "active",
            "confidence": "best-effort", "painted_date": "", "as_of": "", "sources": SOURCE,
            "notes": "; ".join(notes), "country": country,
        })
    return rows, skipped


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()


def names_agree(a, b):
    ta, tb = set(_norm(a)) - {"the", "one"}, set(_norm(b)) - {"the", "one"}
    if not ta or not tb:
        return True
    return ta <= tb or tb <= ta or len(ta & tb) / len(ta | tb) >= 0.5


def merge(existing, incoming, today):
    """Add new tails; never overwrite existing ones. Disagreements go to the returned conflict list."""
    by_reg = {r["registration"]: r for r in existing}
    added, agreed, conflicts = 0, 0, []
    for c in incoming:
        cur = by_reg.get(c["registration"])
        if cur is None:
            c["as_of"] = today
            existing.append(c)
            by_reg[c["registration"]] = c
            added += 1
            continue
        if not cur.get("country"):
            cur["country"] = c["country"]
        problems = []
        if not names_agree(cur["livery_name"], c["livery_name"]):
            problems.append(("livery_name", cur["livery_name"], c["livery_name"]))
        if cur["status"] == "retired":
            problems.append(("status", "retired", "active (site lists it as currently in special livery)"))
        for field, kept, said in problems:
            conflicts.append({"registration": c["registration"], "field": field, "kept": kept,
                              "source_says": said, "source": SOURCE})
        if not problems:
            agreed += 1
            if SOURCE not in cur["sources"]:
                cur["sources"] = (cur["sources"] + ";" + SOURCE).strip(";")
    return added, agreed, conflicts
