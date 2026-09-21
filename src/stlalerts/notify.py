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
    "arrival": "Arrival", "departure": "Departure",
    "ground": "On the ground at STL", "inbound": "Inbound to STL",
}


def build_message(m, mute_hours, ctl_url):
    tag = " (possible diversion)" if m["tag"] == "diversion?" else ""
    title = f"Special livery: {m['livery_name']} ({m['registration']})"
    lines = [
        f"{m['airline']} {m['callsign'] or 'no callsign'} - {PHASE_LABEL[m['phase']]}{tag}",
        f"{m['aircraft_type']}  {m['dist_nm']:.0f} nm from STL, "
        + ("on ground" if m["alt_ft"] == "ground" else f"{m['alt_ft']:,} ft"),
    ]
    if m.get("scheduled"):
        lines.append(f"Scheduled {m['scheduled']}" + (f", gate {m['gate']}" if m.get("gate") else ""))
    if m["status"] != "active" or m["confidence"] != "verified":
        lines.append("Note: livery entry is not hand-verified; the tail may have been repainted.")
    click = f"https://globe.adsb.lol/?icao={m['hex']}" if m.get("hex") else ""
    actions = f"http, Mute {mute_hours}h, {ctl_url}, method=POST, body=mute {m['registration']} {mute_hours}, clear=true"
    return {
        "title": title, "body": "\n".join(lines), "click": click,
        "tags": "airplane", "priority": "4", "actions": actions,
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
