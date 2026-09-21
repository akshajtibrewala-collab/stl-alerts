"""Flight-identity helpers shared by the ADS-B and schedule passes.

The ADS-B pass sees ICAO callsigns ("SWA283"); the schedule pass sees IATA flight numbers ("WN 283").
Both are normalised to one key ("SWA283") so the two passes can recognise the same flight.
"""
import re

IATA_TO_ICAO = {
    "WN": "SWA", "AA": "AAL", "DL": "DAL", "UA": "UAL", "AS": "ASA", "F9": "FFT", "AC": "ACA",
    "BA": "BAW", "LH": "DLH", "NK": "NKS", "B6": "JBU", "G4": "AAY", "SY": "SCX", "MX": "MXY",
    "YX": "RPA", "OO": "SKW", "9E": "EDV", "MQ": "ENY", "OH": "JIA", "G7": "GJS", "C5": "UCA",
    "ZW": "AWI", "QX": "QXE", "PT": "PDT", "CP": "CPZ", "FX": "FDX", "5X": "UPS",
}


def flight_key(number, icao=None, iata=None):
    """'WN 283' + icao 'SWA' -> 'SWA283'. Returns None if the number can't be parsed."""
    s = re.sub(r"\s+", "", (number or "").upper())
    if len(s) < 3:
        return None
    code, rest = (iata or s[:2]).upper(), s[2:]
    if iata and s.startswith(iata.upper()):
        rest = s[len(iata):]
    digits = re.sub(r"\D", "", rest).lstrip("0")
    if not digits:
        return None
    prefix = (icao or IATA_TO_ICAO.get(code) or code).upper()
    return f"{prefix}{digits}"


def callsign_key(callsign):
    """'SWA0283 ' -> 'SWA283'. Non-airline callsigns (tail-number style, blanks) -> None."""
    m = re.fullmatch(r"([A-Z]{3})0*(\d{1,4})[A-Z]?", (callsign or "").strip().upper())
    return f"{m.group(1)}{m.group(2)}" if m else None
