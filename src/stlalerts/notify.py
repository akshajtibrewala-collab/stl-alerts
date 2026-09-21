"""ntfy.sh notifications + the 'mute this tail' control channel.

The Mute button is an ntfy `http` action that POSTs "mute <REG> <hours>" to a
second, secret topic (<topic>-ctl). Each poll run reads that topic and applies
the mutes, so no server or token is needed. The secret topic name is the only
credential, so keep it in GitHub secrets.
"""
import json
import urllib.request

from . import state as st

PHASE_LABEL = {
    "arrival": "Now arriving at STL", "departure": "Now departing STL",
    "ground": "On the ground at STL", "inbound": "Inbound to STL",
}


def flightradar24_url(reg):
    """Tapping any alert opens FlightRadar24's live page for this tail."""
    return f"https://www.flightradar24.com/data/aircraft/{reg}"


def build_message(m, mute_hours, ctl_url):
    """Live sighting from the ADS-B pass. Always titled 'Live:' so it is never mistaken for a plan."""
    tag = " (possible diversion)" if m["tag"] == "diversion?" else ""
    title = f"Live: {m['livery_name']} ({m['registration']})"
    lines = [
        f"{m['airline']} {m['callsign'] or 'no callsign'} - {PHASE_LABEL[m['phase']]}{tag}",
        f"LIVE sighting (ADS-B): {m['aircraft_type']}  {m['dist_nm']:.0f} nm from STL, "
        + ("on ground" if m["alt_ft"] == "ground" else f"{m['alt_ft']:,} ft"),
    ]
    if m.get("scheduled"):
        lines.append(f"Scheduled {m['scheduled']}" + (f", gate {m['gate']}" if m.get("gate") else ""))
    if m["status"] != "active" or m["confidence"] != "verified":
        lines.append("Note: livery entry is not hand-verified; the tail may have been repainted.")
    click = flightradar24_url(m["registration"])
    actions = f"http, Mute {mute_hours}h, {ctl_url}, method=POST, body=mute {m['registration']} {mute_hours}, clear=true"
    return {
        "title": title, "body": "\n".join(lines), "click": click,
        "tags": "airplane", "priority": "4", "actions": actions,
    }


def build_schedule_message(a, mute_hours, ctl_url):
    """Alert from the schedule pass: planned / swap_in / swap_out / swap_change."""
    kind = a["alert_type"]
    when = a["scheduled"]
    if a["gate"]:
        when += f", gate {a['gate']}"
    if a["terminal"]:
        when += f", terminal {a['terminal']}"
    place = f"{'to' if a['direction'] == 'Departure' else 'from'} {a['other_airport']}" if a["other_airport"] else ""
    flight = f"{a['airline']} {a['flight_number']} {a['direction'].lower()} {place}".strip()
    div = " (possible diversion)" if a["tag"] == "diversion?" else ""
    if kind == "planned":
        title = f"Planned: {a['livery_name']} ({a['registration']})"
        lines = [f"{flight}{div}", f"Scheduled {when}",
                 "PLANNED from the schedule, not a live sighting. The tail can still be swapped. "
                 "You'll get a separate LIVE alert when it's actually spotted flying near STL."]
    elif kind == "swap_in":
        title = f"Swapped IN: {a['livery_name']} ({a['registration']})"
        lines = [f"{flight}{div}", f"Scheduled {when}",
                 f"Now assigned to this flight instead of {a['old_reg']} (standard livery). Still a plan, not a sighting."]
    elif kind == "swap_change":
        title = f"Livery changed: {a['old_livery']} -> {a['new_livery']}"
        lines = [f"{flight}{div}", f"Scheduled {when}",
                 f"Tail changed {a['old_reg']} -> {a['new_reg']}; both are special liveries. Still a plan, not a sighting."]
    else:  # swap_out
        title = f"Swapped OUT: {a['old_livery']} ({a['old_reg']})"
        new = f"{a['new_reg']}" + (f" ({a['new_livery']})" if a["new_livery"] else " (standard livery)")
        lines = [f"{flight}{div}", f"Scheduled {when}", f"No longer assigned; now {new}."]
    if a["status"] != "active" or a["confidence"] != "verified":
        lines.append("Note: livery entry is not hand-verified; the tail may have been repainted.")
    tail = a["new_reg"] if kind != "swap_out" else a["old_reg"]
    return {
        "title": title, "body": "\n".join(lines), "tags": "calendar,airplane",
        "priority": "4" if kind != "planned" else "3",
        "click": flightradar24_url(tail),
        "actions": f"http, Mute {mute_hours}h, {ctl_url}, method=POST, body=mute {a['registration']} {mute_hours}, clear=true",
    }


def send(server, topic, msg):
    req = urllib.request.Request(
        f"{server.rstrip('/')}/{topic}", data=msg["body"].encode("utf-8"), method="POST",
        headers={"Title": msg["title"], "Tags": msg["tags"], "Priority": msg["priority"],
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
