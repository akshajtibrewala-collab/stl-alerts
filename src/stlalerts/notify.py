"""ntfy.sh notifications + the 'mute this tail' control channel.

The Mute button is an ntfy `http` action that POSTs "mute <REG> <hours>" to a
second, secret topic (<topic>-ctl). Each poll run reads that topic and applies
the mutes, so no server or token is needed. The secret topic name is the only
credential, so keep it in GitHub secrets.
"""
import json
import re
import urllib.request
from email.header import Header

from . import state as st

# Live (ADS-B) status words. "Inbound" only ever appears on live alerts; scheduled alerts say Planned/Swapped.
LIVE_STATUS = {"inbound": "Inbound", "arrival": "Inbound", "departure": "Airborne", "ground": "On ground"}
ARROW = {"Departure": "→", "Arrival": "←"}   # -> going to that airport, <- coming from it


def flightradar24_url(reg):
    """Tapping any alert opens FlightRadar24's live page for this tail."""
    return f"https://www.flightradar24.com/data/aircraft/{reg}"


def short_airline(name):
    """'Southwest Airlines' -> 'Southwest', 'Delta Air Lines' -> 'Delta'; 'British Airways' stays."""
    return re.sub(r"\s+(Airlines?|Air Lines)$", "", (name or "").strip())


def _unverified(x):
    """Only rows the database itself marks `unverified` get the tag."""
    return x.get("status") == "unverified"


def build_message(m, mute_hours, ctl_url):
    """Live sighting from the ADS-B pass."""
    title = f"{LIVE_STATUS[m['phase']]}: {m['livery_name']} ({m['registration']})"
    parts = [f"{short_airline(m['airline'])} {m['callsign'] or 'no callsign'}"]
    parts.append("at STL" if m["phase"] == "ground" else f"{m['dist_nm']:.0f} nm")
    if m["phase"] != "ground" and m["alt_ft"] != "ground":
        parts.append(f"{m['alt_ft']:,} ft")
    if m["tag"] == "diversion?":
        parts.append("diversion?")
    if _unverified(m):
        parts.append("(unverified)")
    return {
        "title": title, "body": " · ".join(parts), "click": flightradar24_url(m["registration"]),
        "tags": "airplane", "priority": "4",
        "actions": f"http, Mute {mute_hours}h, {ctl_url}, method=POST, body=mute {m['registration']} {mute_hours}, clear=true",
    }


def build_schedule_message(a, mute_hours, ctl_url):
    """Alert from the schedule pass: planned / swap_in / swap_out / swap_change."""
    kind = a["alert_type"]
    dep = a["direction"] == "Departure"
    other = f" {ARROW[a['direction']]} {a['other_airport']}" if a["other_airport"] else ""
    line1 = f"{short_airline(a['airline'])} {a['flight_number']}{other}"
    if a["tag"] == "diversion?":
        line1 += " · diversion?"
    line2 = [("Dep " if dep else "Arr ") + a["scheduled"]]
    if a["terminal"]:
        line2.append(f"Terminal {a['terminal']}")
    if a["gate"]:
        line2.append(f"Gate {a['gate']}")
    if kind == "planned":
        title = f"Planned: {a['livery_name']} ({a['registration']})"
    elif kind == "swap_in":
        title = f"Swapped in: {a['livery_name']} ({a['registration']})"
        line2.append(f"replaces {a['old_reg']}")
    elif kind == "swap_change":
        title = f"Swapped: {a['old_livery']} → {a['new_livery']}"
        line2.append(f"{a['old_reg']} → {a['new_reg']}")
    else:  # swap_out
        title = f"Swapped out: {a['old_livery']} ({a['old_reg']})"
        line2.append(f"now {a['new_reg']}")
    if _unverified(a):
        line2.append("(unverified)")
    tail = a["old_reg"] if kind == "swap_out" else a["new_reg"]
    return {
        "title": title, "body": line1 + "\n" + " · ".join(line2), "tags": "calendar",
        "priority": "3" if kind == "planned" else "4", "click": flightradar24_url(tail),
        "actions": f"http, Mute {mute_hours}h, {ctl_url}, method=POST, body=mute {a['registration']} {mute_hours}, clear=true",
    }


def _header(value):
    """HTTP headers are latin-1; anything else (e.g. the arrow in a swap title) goes as an RFC 2047 encoded-word."""
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        return Header(value, "utf-8").encode()


def send(server, topic, msg):
    req = urllib.request.Request(
        f"{server.rstrip('/')}/{topic}", data=msg["body"].encode("utf-8"), method="POST",
        headers={"Title": _header(msg["title"]), "Tags": msg["tags"], "Priority": msg["priority"],
                 **({"Click": msg["click"]} if msg.get("click") else {}),
                 **({"Actions": msg["actions"]} if msg.get("actions") else {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def apply_control_messages(state, server, ctl_topic, max_hours=24 * 30):
    """Read new 'mute REG [hours]' / 'unmute REG' messages from the control topic."""
    url = f"{server.rstrip('/')}/{ctl_topic}/json?poll=1&since={state['ctl_since']}"
    with urllib.request.urlopen(url, timeout=20) as r:
        lines = [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]
    applied = []
    for ev in lines:
        if ev.get("event") != "message":
            continue
        state["ctl_since"] = ev["id"]
        parts = ev.get("message", "").split()
        if len(parts) >= 2 and parts[0].lower() == "mute":
            try:
                hours = min(float(parts[2]) if len(parts) > 2 else 24, max_hours)
            except ValueError:
                continue
            st.mute(state, parts[1].upper(), hours)
            applied.append(f"muted {parts[1].upper()} for {hours:g}h")
        elif len(parts) >= 2 and parts[0].lower() == "unmute":
            state["mutes"].pop(parts[1].upper(), None)
            applied.append(f"unmuted {parts[1].upper()}")
    return applied
