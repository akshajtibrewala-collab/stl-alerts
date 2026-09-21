import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from stlalerts import airportwebcams as awc, liveries, notify, wikipedia  # noqa: E402
from stlalerts import state as st  # noqa: E402
from stlalerts.match import classify, find_matches  # noqa: E402

CFG = json.loads((ROOT / "config" / "config.json").read_text())
AIRPORT, G = CFG["airport"], CFG["geometry"]


def ac(**kw):
    base = {"hex": "abc123", "r": "N8977G", "flight": "SWA100 ", "lat": AIRPORT["lat"], "lon": AIRPORT["lon"],
            "alt_baro": 3000, "baro_rate": -700, "track": 0, "t": "B38M"}
    base.update(kw)
    return base


def row(reg="N8977G", airline="Southwest Airlines", status="active", kind="livery"):
    return {"registration": reg, "icao24": "", "airline": airline, "aircraft_type": "B38M",
            "livery_name": "Louisiana One", "kind": kind, "status": status, "confidence": "verified"}


class Classify(unittest.TestCase):
    def test_ground_at_stl(self):
        self.assertEqual(classify(ac(alt_baro="ground"), AIRPORT, G)[0], "ground")

    def test_ground_elsewhere_ignored(self):
        self.assertIsNone(classify(ac(alt_baro="ground", lat=39.5), AIRPORT, G)[0])

    def test_arrival_heading_toward(self):
        # 20 nm south of STL heading north (0 deg), descending
        self.assertEqual(classify(ac(lat=AIRPORT["lat"] - 0.33, track=0), AIRPORT, G)[0], "arrival")

    def test_departure_heading_away(self):
        self.assertEqual(classify(ac(lat=AIRPORT["lat"] - 0.33, track=180, baro_rate=2000), AIRPORT, G)[0], "departure")

    def test_cruise_overflight_ignored(self):
        self.assertIsNone(classify(ac(lat=AIRPORT["lat"] - 0.33, alt_baro=38000, baro_rate=0), AIRPORT, G)[0])

    def test_inbound_descending_far_out(self):
        far = ac(lat=AIRPORT["lat"] - 1.2, track=0, alt_baro=15000, baro_rate=-1500)  # ~72 nm
        self.assertEqual(classify(far, AIRPORT, G)[0], "inbound")

    def test_far_and_cruising_ignored(self):
        far = ac(lat=AIRPORT["lat"] - 1.2, track=0, alt_baro=36000, baro_rate=0)
        self.assertIsNone(classify(far, AIRPORT, G)[0])


class Matching(unittest.TestCase):
    def setUp(self):
        self.watch = CFG["watch_airlines"]

    def test_nonstl_airline_still_alerts_as_possible_diversion(self):
        r = row("G-EUPJ", "British Airways")
        by_reg, by_hex = liveries.alertable([r], {"active"}, {"livery"})
        r2 = row("N999QF", "Qantas")
        by_reg, by_hex = liveries.alertable([r2], {"active"}, {"livery"})
        m = find_matches([ac(r="N999QF", alt_baro="ground")], by_reg, by_hex, AIRPORT, G, self.watch)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["tag"], "diversion?")

    def test_watch_airline_tagged_routine(self):
        by_reg, by_hex = liveries.alertable([row()], {"active"}, {"livery"})
        m = find_matches([ac(alt_baro="ground")], by_reg, by_hex, AIRPORT, G, self.watch)
        self.assertEqual(m[0]["tag"], "routine")

    def test_retired_and_excluded_kinds_do_not_alert(self):
        by_reg, _ = liveries.alertable([row(status="retired"), row("N1", kind="decal")], {"active"}, {"livery"})
        self.assertEqual(by_reg, {})

    def test_unknown_tail_ignored(self):
        by_reg, by_hex = liveries.alertable([row()], {"active"}, {"livery"})
        self.assertEqual(find_matches([ac(r="N1ZZZ", alt_baro="ground")], by_reg, by_hex, AIRPORT, G, self.watch), [])


class DedupeAndMute(unittest.TestCase):
    def setUp(self):
        self.state = {"alerted": {}, "mutes": {}, "ctl_since": "24h"}
        self.m = {"registration": "N8977G", "callsign": "SWA100"}

    def test_duplicate_suppressed_then_expires(self):
        t0 = st.now()
        self.assertTrue(st.should_alert(self.state, self.m, 6, t0))
        st.record(self.state, self.m, t0)
        self.assertFalse(st.should_alert(self.state, self.m, 6, t0 + timedelta(hours=1)))
        self.assertTrue(st.should_alert(self.state, self.m, 6, t0 + timedelta(hours=7)))

    def test_new_callsign_is_new_flight(self):
        st.record(self.state, self.m)
        self.assertTrue(st.should_alert(self.state, {**self.m, "callsign": "SWA200"}, 6))

    def test_mute_blocks_all_flights_for_tail(self):
        st.mute(self.state, "N8977G", 24)
        self.assertFalse(st.should_alert(self.state, {**self.m, "callsign": "SWA200"}, 6))
        self.assertTrue(st.should_alert(self.state, {"registration": "N1", "callsign": "X"}, 6))

    def test_mute_expires(self):
        st.mute(self.state, "N8977G", 24, at=st.now() - timedelta(hours=25))
        self.assertTrue(st.should_alert(self.state, self.m, 6))


class Notify(unittest.TestCase):
    def test_message_and_mute_action(self):
        m = {"registration": "N8977G", "hex": "ac0f03", "callsign": "SWA283", "airline": "Southwest Airlines",
             "livery_name": "Louisiana One", "aircraft_type": "B38M", "phase": "arrival", "dist_nm": 12.3,
             "alt_ft": 3400, "tag": "diversion?", "status": "active", "confidence": "verified"}
        msg = notify.build_message(m, 24, "https://ntfy.sh/secret-ctl")
        self.assertIn("Louisiana One", msg["title"])
        self.assertIn("Now arriving at STL (possible diversion)", msg["body"])
        self.assertTrue(msg["title"].startswith("Live: "))
        self.assertEqual(msg["click"], "https://www.flightradar24.com/data/aircraft/N8977G")
        self.assertIn("body=mute N8977G 24", msg["actions"])
        msg["title"].encode("latin-1")  # HTTP headers must be latin-1 safe

    def test_control_messages_apply(self):
        state = {"alerted": {}, "mutes": {}, "ctl_since": "24h"}
        body = "\n".join(json.dumps(e) for e in [
            {"event": "open"},
            {"event": "message", "id": "a1", "message": "mute n8977g 12"},
            {"event": "message", "id": "a2", "message": "mute N1776R"},
            {"event": "message", "id": "a3", "message": "unmute N1776R"},
        ]).encode()
        fake = mock.MagicMock()
        fake.__enter__.return_value.read.return_value = body
        with mock.patch("urllib.request.urlopen", return_value=fake):
            notify.apply_control_messages(state, "https://ntfy.sh", "t-ctl")
        self.assertIn("N8977G", state["mutes"])
        self.assertNotIn("N1776R", state["mutes"])
        self.assertEqual(state["ctl_since"], "a3")


SAMPLE_WIKI = """current and former special liveries
|-
! colspan="5" | Active
|-
! Name !! Year !! Description
|-
|''Louisiana One''
|2018
|The flag of [[Louisiana]] is applied.
|N946WN (previous)<br />N8977G (current)
|[[File:x.jpg]]
|-
! colspan="5" | Former
|-
|''Old One''
|1999
|Old.
|N12SW
|
|}
"""


class WikipediaAndMerge(unittest.TestCase):
    def test_parse_handles_long_nnumbers_and_status(self):
        rows = wikipedia.parse_southwest(SAMPLE_WIKI)
        got = {r["registration"]: r["status"] for r in rows}
        self.assertEqual(got, {"N946WN": "retired", "N8977G": "active", "N12SW": "retired"})

    def test_refresh_never_overwrites_verified(self):
        import refresh_liveries as rl
        existing = [{**row(), "sources": "manual", "confidence": "verified", "painted_date": "", "description": "", "as_of": ""}]
        cand = [{**row(status="retired"), "sources": "wikipedia", "confidence": "best-effort", "painted_date": "", "description": "", "as_of": ""}]
        added, updated, conflicts = rl.merge(existing, cand, "2026-01-01")
        self.assertEqual((added, updated, len(conflicts)), (0, 0, 1))
        self.assertEqual(existing[0]["status"], "active")

    def test_shipped_database_is_sane(self):
        rows = liveries.load(ROOT / "data" / "liveries.csv")
        regs = [r["registration"] for r in rows]
        self.assertEqual(len(regs), len(set(regs)), "duplicate registrations")
        for r in rows:
            self.assertIn(r["status"], {"active", "unverified", "retired"}, r["registration"])
            self.assertIn(r["kind"], {"livery", "retro", "decal", "partnership", "alliance", "other"}, r["registration"])


SAMPLE_AWC = """<p>Last Updated: 15-September-2026. Click</p>
<table id="tablepress-8"><thead><tr class="row-1"><th>Country</th></tr></thead><tbody>
<tr class="row-2"><td>USA</td><td>Southwest Airlines</td><td>Boeing 737-8</td><td><a href="x">N8977G</a></td><td>Louisiana One</td></tr>
<tr class="row-3"><td>USA</td><td>Southwest Airlines</td><td>Boeing 737-7</td><td><a href="x">N906WN</a></td><td>Charles E Taylor (sticker)</td></tr>
<tr class="row-4"><td>Qatar</td><td>Qatar Airways</td><td>Airbus A350</td><td><a href="x">A7-ALA</a></td><td>oneworld (#NEW at 15-Sep-26)</td></tr>
<tr class="row-5"><td>Ethiopia</td><td>Ethiopian Airlines</td><td>Various</td><td>Various</td><td>80 Years (sticker)</td></tr>
<tr class="row-6"><td>USA</td><td>Southwest Airlines</td><td>Boeing 737-8</td><td><a href="x">N871HK</a></td><td>Desert Gold Retro</td></tr>
<tr class="row-7"><td>USA</td><td>Some Air</td><td>B738</td><td><a href="x">N559AS</a></td><td>New Paint</td></tr>
</tbody></table>"""


class AirportWebcams(unittest.TestCase):
    def test_parse(self):
        rows, skipped = awc.parse(SAMPLE_AWC)
        by = {r["registration"]: r for r in rows}
        self.assertEqual(set(by), {"N8977G", "N906WN", "A7-ALA", "N871HK", "N559AS"})
        self.assertEqual([s[0] for s in skipped], ["not a registration"])
        self.assertEqual(by["N906WN"]["kind"], "decal")
        self.assertEqual(by["A7-ALA"]["kind"], "alliance")
        self.assertEqual(by["A7-ALA"]["livery_name"], "oneworld")
        self.assertIn("flagged NEW", by["A7-ALA"]["notes"])
        self.assertIn("site updated 15-September-2026", by["N8977G"]["notes"])

    def test_merge_never_overwrites_and_logs_conflicts(self):
        rows, _ = awc.parse(SAMPLE_AWC)
        mk = lambda reg, name, status="active": {**row(reg), "livery_name": name, "status": status,
                                                  "sources": "wikipedia", "country": "", "notes": ""}
        existing = [mk("N8977G", "Louisiana One"), mk("N871HK", "The Herbert D. Kelleher"),
                    mk("N559AS", "Salmon-Thirty-Salmon", "retired")]
        added, agreed, conflicts = awc.merge(existing, rows, "2026-09-21")
        self.assertEqual((added, agreed), (2, 1))
        got = {(c["registration"], c["field"]) for c in conflicts}
        self.assertEqual(got, {("N871HK", "livery_name"), ("N559AS", "livery_name"), ("N559AS", "status")})
        by = {r["registration"]: r for r in existing}
        self.assertEqual(by["N871HK"]["livery_name"], "The Herbert D. Kelleher")  # untouched
        self.assertEqual(by["N559AS"]["status"], "retired")                        # untouched
        self.assertIn("airportwebcams.net", by["N8977G"]["sources"])

    def test_default_config_excludes_alliance_and_decal(self):
        kinds = CFG["alerts"]["kinds"]
        self.assertNotIn("alliance", kinds)
        self.assertNotIn("decal", kinds)


if __name__ == "__main__":
    unittest.main()
