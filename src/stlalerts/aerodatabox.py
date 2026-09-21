"""AeroDataBox client + parser for the airport arrivals/departures (FIDS) endpoint.

Endpoint and fields follow AeroDataBox's official OpenAPI spec (GET /flights/airports/{codeType}/{code},
"FIDS by relative time", TIER 2 = 2 API units per call). Run scripts/probe_aerodatabox.py to see live output.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .flights import flight_key

PROVIDERS = {
    # AeroDataBox's own portal (aerodatabox.com). Confirmed from their official OpenAPI spec.
    "direct": {
        "base": "https://api.aerodatabox.com",
        "headers": lambda key: {"X-Api-Key": key},
    },
    "rapidapi": {
        "base": "https://aerodatabox.p.rapidapi.com",
        "headers": lambda key: {"x-rapidapi-key": key, "x-rapidapi-host": "aerodatabox.p.rapidapi.com"},
    },
    "apimarket": {
        "base": "https://prod.api.market/api/v1/aedbx/aerodatabox",
        "headers": lambda key: {"x-api-market-key": key},
    },
    # Direct-portal or anything else: set ADB_BASE_URL and ADB_AUTH_HEADER (header name that carries the key).
    "custom": {
        "base": os.environ.get("ADB_BASE_URL", ""),
        "headers": lambda key: {os.environ.get("ADB_AUTH_HEADER", "x-api-key"): key},
    },
}
# Statuses we never alert on: the flight is not (or is no longer) operating to/from this airport as planned.
CANCELLED = {"canceled", "cancelled", "canceleduncertain", "diverted"}


class AeroDataBoxError(Exception):
    def __init__(self, status, body=""):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


class Client:
    def __init__(self, key, provider="rapidapi", base_url=None):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}; use one of {sorted(PROVIDERS)}")
        p = PROVIDERS[provider]
        self.base = (base_url or p["base"]).rstrip("/")
        if not self.base:
            raise ValueError("no base URL: set ADB_BASE_URL for the 'custom' provider")
        self.headers = {**p["headers"](key), "Accept": "application/json",
                        "User-Agent": "stl-livery-alerts/0.1 (personal hobby project)"}

    def fids(self, icao, offset_minutes=-30, duration_minutes=720, cargo=True):
        """Arrivals + departures for a relative window (max 12 h). Returns (json, ratelimit_headers)."""
        q = urllib.parse.urlencode({
            "offsetMinutes": offset_minutes, "durationMinutes": duration_minutes, "direction": "Both",
            "withLeg": "false", "withCancelled": "false", "withCodeshared": "false",
            "withCargo": str(cargo).lower(), "withPrivate": "false", "withLocation": "false",
        })
        req = urllib.request.Request(f"{self.base}/flights/airports/icao/{icao}?{q}", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                hdrs = {k.lower(): v for k, v in r.headers.items() if "ratelimit" in k.lower() or "unit" in k.lower()}
                return json.load(r), hdrs
        except urllib.error.HTTPError as e:
            raise AeroDataBoxError(e.code, e.read().decode("utf-8", "replace"))


def parse_dt(s):
    """AeroDataBox time strings ('2026-09-21 19:05-05:00', '2026-09-22 00:05Z') -> aware UTC datetime."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def fmt_local(s):
    """'2026-09-21 19:05-05:00' -> 'Mon 7:05 PM' (wall-clock time at the airport)."""
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return s or ""
    return f"{dt:%a} {dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def _time(mv, *names):
    """Return (utc_string, local_string) for the first time field present, in either API naming style."""
    for n in names:
        v = mv.get(n)
        if isinstance(v, dict):
            return v.get("utc", ""), v.get("local", "")
    for n in names:  # older flat style: scheduledTimeUtc / scheduledTimeLocal
        if mv.get(n + "Utc") or mv.get(n + "Local"):
            return mv.get(n + "Utc", ""), mv.get(n + "Local", "")
    return "", ""


def parse_fids(data):
    """Normalise a FIDS response into flat flight dicts. Tolerant of missing fields."""
    out = []
    for list_key, direction in (("departures", "Departure"), ("arrivals", "Arrival")):
        for it in data.get(list_key) or []:
            mv = it.get("movement") or it.get(direction.lower()) or {}
            sched_utc, sched_local = _time(mv, "scheduledTime")
            rev_utc, rev_local = _time(mv, "revisedTime", "predictedTime")
            ac = it.get("aircraft") or {}
            al = it.get("airline") or {}
            number = (it.get("number") or "").strip()
            other = mv.get("airport") or {}
            out.append({
                "direction": direction,
                "number": number,
                "flight_key": flight_key(number, al.get("icao"), al.get("iata")),
                "airline": al.get("name", ""),
                "airline_icao": (al.get("icao") or "").upper(),
                "status": (it.get("status") or "").strip(),
                "reg": re.sub(r"\s+", "", (ac.get("reg") or "")).upper(),
                "mode_s": (ac.get("modeS") or "").lower(),
                "aircraft_model": ac.get("model", ""),
                "sched_utc": parse_dt(sched_utc),
                "best_utc": parse_dt(rev_utc) or parse_dt(sched_utc),
                "local_time": rev_local or sched_local,
                "local_date": (sched_local or rev_local)[:10],
                "gate": mv.get("gate") or "",
                "terminal": mv.get("terminal") or "",
                "other_airport": other.get("iata") or other.get("icao") or "",
                "is_cargo": bool(it.get("isCargo")),
            })
    return out
