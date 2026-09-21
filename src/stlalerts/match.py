"""Turn raw ADS-B aircraft into 'phase' classifications and livery matches.

Matching is deliberately NOT scoped by airline, so a diverted aircraft from a
carrier that never serves STL still alerts. Airline only drives the
routine/diversion presentation tag.
"""
from .geo import angle_diff, bearing_deg, distance_nm


def classify(ac, airport, cfg):
    """Return (phase, dist_nm) or (None, dist) if the aircraft isn't interesting.

    phase: ground | arrival | departure | inbound
    """
    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None:
        return None, None
    dist = distance_nm(lat, lon, airport["lat"], airport["lon"])
    alt = ac.get("alt_baro")
    if alt == "ground":
        return ("ground", dist) if dist <= cfg["ground_radius_nm"] else (None, dist)
    if not isinstance(alt, (int, float)):
        return None, dist
    rate = ac.get("baro_rate", ac.get("geom_rate", 0)) or 0
    track = ac.get("track")
    to_airport = bearing_deg(lat, lon, airport["lat"], airport["lon"])
    closing = track is not None and angle_diff(track, to_airport) < 60

    if dist <= cfg["geofence_radius_nm"]:
        if alt >= cfg["overflight_alt_ft"] and abs(rate) < 1000:
            return None, dist  # cruising past
        return ("arrival" if closing else "departure"), dist

    if dist <= cfg["inbound_radius_nm"]:
        heading_at = track is not None and angle_diff(track, to_airport) < 25
        descending = rate < -300 or alt <= cfg["inbound_max_alt_ft"]
        if heading_at and descending and alt < cfg["overflight_alt_ft"]:
            return "inbound", dist
    return None, dist


def find_matches(aircraft, by_reg, by_hex, airport, cfg, watch_airlines):
    watch = {a.lower() for a in watch_airlines}
    out = []
    for ac in aircraft:
        reg = (ac.get("r") or "").upper()
        row = by_reg.get(reg) or by_hex.get((ac.get("hex") or "").lower())
        if not row:
            continue
        phase, dist = classify(ac, airport, cfg)
        if not phase:
            continue
        out.append({
            "registration": row["registration"],
            "hex": ac.get("hex"),
            "callsign": (ac.get("flight") or "").strip(),
            "airline": row["airline"],
            "livery_name": row["livery_name"],
            "kind": row["kind"],
            "status": row["status"],
            "confidence": row["confidence"],
            "aircraft_type": row["aircraft_type"] or ac.get("t", ""),
            "phase": phase,
            "dist_nm": dist,
            "alt_ft": ac.get("alt_baro"),
            "tag": "routine" if row["airline"].lower() in watch else "diversion?",
            # Filled by the schedule-confirmation pass (AeroDataBox) when added:
            "gate": None, "terminal": None, "scheduled": None, "flight_number": None,
        })
    return out
