import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stlalerts import aerodatabox, liveries, notify, schedule  # noqa: E402
from stlalerts import state as st  # noqa: E402
from stlalerts.flights import callsign_key, flight_key  # noqa: E402

CFG = json.loads((ROOT / "config" / "config.json").read_text())
SCFG = CFG["schedule"]
WATCH = set(CFG["watch_airline_codes"])
NOW = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)


def row(reg, livery, airline="Southwest Airlines"):
    return {"registration": reg, "icao24": "", "airline": airline, "aircraft_type": "B38M",
            "livery_name": livery, "kind": "livery", "status": "active", "confidence": "verified"}


BY_REG, BY_HEX = liveries.alertable(
    [row("N8977G", "Louisiana One"), row("N1776R", "Independence One")], {"active"}, {"livery"})


def fids(reg="N8977G", number="WN 283", direction="departures", utc="2026-09-22 00:05Z",
         local="2026-09-21 19:05-05:00", gate="B12", terminal="1", status="Expected", icao="SWA"):
    ac = {"model": "Boeing 737 MAX 8"}
    if reg:
        ac["reg"] = reg
    return {direction: [{
        "movement": {"airport": {"iata": "MDW"}, "scheduledTime": {"utc": utc, "local": local},
                     "gate": gate, "terminal": terminal},
        "number": number, "status": status, "aircraft": ac, "isCargo": False,
        "airline": {"name": "Southwest Airlines", "iata": "WN", "icao": icao}}]}


def poll(state, data, now=NOW):
    leads = []
    alerts = schedule.process(aerodatabox.parse_fids(data), state, BY_REG, BY_HEX, WATCH, now, SCFG, leads)
    return alerts, leads


def new_state():
    return {"alerted": {}, "mutes": {}, "ctl_since": "24h", "flights": {}, "adb": {}}


class TailTracking(unittest.TestCase):
    def test_newly_assigned_special_alerts_as_planned(self):
        alerts, leads = poll(new_state(), fids())
        self.assertEqual([a["alert_type"] for a in alerts], ["planned"])
        a = alerts[0]
        self.assertEqual((a["registration"], a["livery_name"], a["gate"], a["terminal"]),
                         ("N8977G", "Louisiana One", "B12", "1"))
        self.assertEqual(a["scheduled"], "Mon 7:05 PM")
        msg = notify.build_schedule_message(a, 24, "https://ntfy.sh/x-ctl")
        self.assertIn("not a live sighting", msg["body"])
        self.assertIn("gate B12", msg["body"])
        self.assertIn("WN 283", msg["body"])
        self.assertIn("Louisiana One", msg["title"])
        self.assertIn("body=mute N8977G 24", msg["actions"])
        self.assertEqual(len(leads), 1)

    def test_unchanged_tail_never_realerts(self):
        s = new_state()
        poll(s, fids())
        self.assertEqual(poll(s, fids(), NOW + timedelta(hours=6))[0], [])
        self.assertEqual(poll(s, fids(), NOW + timedelta(hours=7))[0], [])

    def test_swap_out_special_to_standard(self):
        s = new_state()
        poll(s, fids("N8977G"))
        alerts, _ = poll(s, fids("N4444X"), NOW + timedelta(hours=5))
        self.assertEqual([a["alert_type"] for a in alerts], ["swap_out"])
        a = alerts[0]
        self.assertEqual((a["registration"], a["old_reg"], a["new_reg"], a["new_livery"]),
                         ("N8977G", "N8977G", "N4444X", None))
        msg = notify.build_schedule_message(a, 24, "u")
        self.assertIn("Swapped OUT", msg["title"])
        self.assertIn("standard livery", msg["body"])
        self.assertEqual(poll(s, fids("N4444X"), NOW + timedelta(hours=6))[0], [])  # and then quiet

    def test_swap_in_standard_to_special(self):
        s = new_state()
        self.assertEqual(poll(s, fids("N4444X"))[0], [])  # standard tail: tracked silently
        alerts, _ = poll(s, fids("N8977G"), NOW + timedelta(hours=5))
        self.assertEqual([a["alert_type"] for a in alerts], ["swap_in"])
        self.assertIn("Swapped IN", notify.build_schedule_message(alerts[0], 24, "u")["title"])

    def test_special_to_different_special_is_one_combined_alert(self):
        s = new_state()
        poll(s, fids("N8977G"))
        alerts, _ = poll(s, fids("N1776R"), NOW + timedelta(hours=5))
        self.assertEqual([a["alert_type"] for a in alerts], ["swap_change"])
        msg = notify.build_schedule_message(alerts[0], 24, "u")
        self.assertIn("Louisiana One -> Independence One", msg["title"])

    def test_no_tail_yet_is_skipped_then_alerts_when_assigned(self):
        s = new_state()
        self.assertEqual(poll(s, fids(None))[0], [])
        alerts, leads = poll(s, fids("N8977G"), NOW + timedelta(hours=6))
        self.assertEqual([a["alert_type"] for a in alerts], ["planned"])
        self.assertTrue(leads[0]["seen_without_tail_first"])
        self.assertAlmostEqual(leads[0]["hours_before"], 3.08, places=1)

    def test_tail_present_at_first_sight_is_flagged_lower_bound(self):
        _, leads = poll(new_state(), fids())
        self.assertFalse(leads[0]["seen_without_tail_first"])

    def test_muted_tail_is_not_alerted_but_tracked(self):
        s = new_state()
        st.mute(s, "N8977G", 24, NOW)
        self.assertEqual(poll(s, fids())[0], [])
        self.assertEqual(s["flights"]["SWA283|2026-09-21|D"]["reg"], "N8977G")

    def test_cancelled_and_departed_flights_ignored(self):
        self.assertEqual(poll(new_state(), fids(status="Canceled"))[0], [])
        old = fids(utc="2026-09-21 13:00Z", local="2026-09-21 08:00-05:00")
        self.assertEqual(poll(new_state(), old)[0], [])

    def test_non_stl_airline_special_still_alerts_tagged_as_possible_diversion(self):
        by_reg, by_hex = liveries.alertable([row("G-XLEE", "Retro", "Qantas")], {"active"}, {"livery"})
        data = fids("G-XLEE", number="QF 1", icao="QFA")
        alerts = schedule.process(aerodatabox.parse_fids(data), new_state(), by_reg, by_hex, WATCH, NOW, SCFG, [])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["tag"], "diversion?")


class CrossPassDedupe(unittest.TestCase):
    def test_live_alert_already_sent_suppresses_planned(self):
        s = new_state()
        st.record(s, {"registration": "N8977G", "callsign": "SWA283"}, NOW)
        alerts, _ = poll(s, fids())
        self.assertEqual(alerts, [])
        self.assertEqual(s["flights"]["SWA283|2026-09-21|D"]["alerted_reg"], "N8977G")

    def test_planned_alert_suppresses_later_adsb_for_same_flight(self):
        s = new_state()
        poll(s, fids())
        self.assertTrue(st.planned_covers(s, "N8977G", "SWA283 ", NOW + timedelta(hours=8)))
        self.assertFalse(st.planned_covers(s, "N8977G", "SWA999", NOW))       # different flight
        self.assertFalse(st.planned_covers(s, "N1776R", "SWA283", NOW))       # different tail
        self.assertFalse(st.planned_covers(s, "N8977G", "N8977G", NOW))       # tail-number callsign

    def test_failed_send_is_rolled_back_and_retried(self):
        s = new_state()
        alerts, _ = poll(s, fids())
        schedule.undo(s, alerts[0])
        retry, _ = poll(s, fids(), NOW + timedelta(minutes=10))
        self.assertEqual([a["alert_type"] for a in retry], ["planned"])


class Polling(unittest.TestCase):
    def test_first_run_polls_then_follows_the_daily_schedule(self):
        s = new_state()
        now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(schedule.due(s, now, SCFG), "base")                     # first ever run
        schedule.record_poll(s, now, SCFG, "base")
        self.assertIsNone(schedule.due(s, now + timedelta(minutes=30), SCFG))     # between anchors
        self.assertEqual(schedule.due(s, datetime(2026, 9, 21, 17, 5, tzinfo=timezone.utc), SCFG), "base")

    def test_budget_exhaustion_stops_polling(self):
        s = new_state()
        schedule.budget(s, NOW, SCFG)
        s["adb"]["units"] = SCFG["monthly_budget_units"] - SCFG["budget_reserve_units"] - 1
        self.assertIsNone(schedule.due(s, NOW, SCFG))

    def test_backoff_blocks_until_expiry(self):
        s = new_state()
        schedule.block(s, NOW, 2)
        self.assertIsNone(schedule.due(s, NOW + timedelta(hours=1), SCFG))
        self.assertEqual(schedule.due(s, NOW + timedelta(hours=3), SCFG), "base")

    def test_extra_poll_when_special_flight_is_near_but_capped(self):
        s = new_state()
        schedule.record_poll(s, NOW, SCFG, "base")           # the 15:00Z anchor has been served
        soon = NOW + timedelta(hours=4)                       # special flight departs 19:00Z
        s["flights"]["SWA283|2026-09-21|D"] = {"reg": "N8977G", "livery": "Louisiana One",
                                                "sched_utc": soon.isoformat(), "alerted_reg": "N8977G"}
        later = NOW + timedelta(minutes=100)                  # 16:40Z: inside the 3 h watch horizon
        self.assertEqual(schedule.due(s, later, SCFG), "watch")
        for _ in range(SCFG["max_extra_calls_per_day"]):
            schedule.record_poll(s, later, SCFG, "watch")
        self.assertNotEqual(schedule.due(s, later + timedelta(minutes=100), SCFG), "watch")  # daily cap reached

    def test_far_off_special_flight_does_not_trigger_extra_poll(self):
        s = new_state()
        schedule.record_poll(s, NOW, SCFG, "base")
        s["flights"]["SWA283|2026-09-21|D"] = {"reg": "N8977G", "livery": "Louisiana One",
                                                "sched_utc": (NOW + timedelta(hours=9)).isoformat()}
        self.assertIsNone(schedule.due(s, NOW + timedelta(minutes=100), SCFG))

    def test_no_extra_poll_without_special_flight(self):
        s = new_state()
        schedule.record_poll(s, NOW, SCFG, "base")
        self.assertIsNone(schedule.due(s, NOW + timedelta(minutes=100), SCFG))

    def test_ledger_syncs_to_the_apis_own_unit_count(self):
        s = new_state()
        schedule.record_poll(s, NOW, SCFG, "base", {"x-ratelimit-api-units-limit": "400",
                                                    "x-ratelimit-api-units-remaining": "384"})
        self.assertEqual(s["adb"]["units"], 16)

    def test_month_rollover_resets_ledger(self):
        s = new_state()
        schedule.record_poll(s, NOW, SCFG, "base")
        self.assertEqual(s["adb"]["units"], SCFG["units_per_call"])
        schedule.budget(s, NOW + timedelta(days=30), SCFG)
        self.assertEqual(s["adb"]["units"], 0)

    def test_daily_poll_count_follows_remaining_budget(self):
        s = new_state()
        n, anchors, calls_left, days_left = schedule.plan(s, datetime(2026, 10, 1, 0, 5, tzinfo=timezone.utc), SCFG)
        self.assertEqual((n, calls_left, days_left), (6, 190, 31))
        self.assertEqual(len(anchors), 6)
        low = new_state()
        schedule.budget(low, NOW, SCFG)
        low["adb"]["units"] = 370          # only 5 calls left with 10 days to go -> under one a day
        self.assertEqual(schedule.plan(low, NOW, SCFG)[0], 0)
        self.assertIsNone(schedule.due({**low, "adb": {**low["adb"], "last_poll": NOW.isoformat()}}, NOW + timedelta(hours=8), SCFG))

    def test_a_whole_month_of_scheduled_polls_never_exceeds_the_budget(self):
        s = new_state()
        total = 0
        for day in range(1, 31):
            now = datetime(2026, 9, day, 0, 5, tzinfo=timezone.utc)
            n = schedule.plan(s, now, SCFG)[0]
            for _ in range(n):
                schedule.record_poll(s, now, SCFG, "base")
            total += n
        self.assertLessEqual(s["adb"]["units"], SCFG["monthly_budget_units"] - SCFG["budget_reserve_units"])
        self.assertGreaterEqual(total, 170)   # and it actually uses (almost) all of it

    def test_schedule_table_is_sane(self):
        for k, hours in SCFG["poll_schedule_utc"].items():
            self.assertEqual(len(hours), int(k))
            self.assertEqual(len(set(hours)), len(hours))

class Parsing(unittest.TestCase):
    def test_flight_keys_agree_across_passes(self):
        self.assertEqual(flight_key("WN 283", "SWA", "WN"), "SWA283")
        self.assertEqual(flight_key("WN0283"), "SWA283")
        self.assertEqual(callsign_key("SWA0283 "), "SWA283")
        self.assertEqual(flight_key("9E 5012"), "EDV5012")
        self.assertIsNone(callsign_key("N8977G"))
        self.assertIsNone(callsign_key(""))

    def test_parse_tolerates_missing_fields_and_old_flat_style(self):
        data = {"arrivals": [{"number": "AA 100", "movement": {"scheduledTimeUtc": "2026-09-22 01:00Z",
                                                               "scheduledTimeLocal": "2026-09-21 20:00-05:00"},
                              "airline": {"iata": "AA"}},
                             {"number": "", "airline": {}}]}
        f = aerodatabox.parse_fids(data)
        self.assertEqual(f[0]["flight_key"], "AAL100")
        self.assertEqual(f[0]["reg"], "")
        self.assertEqual(f[0]["local_date"], "2026-09-21")
        self.assertIsNone(f[1]["flight_key"])

    def test_empty_204_response_is_an_empty_board_not_a_crash(self):
        from unittest import mock
        fake = mock.MagicMock()
        r = fake.__enter__.return_value
        r.status, r.read.return_value, r.headers = 204, b"", {"X-RateLimit-API-Units-Remaining": "388"}
        with mock.patch("urllib.request.urlopen", return_value=fake):
            data, hdrs = aerodatabox.Client("k", "rapidapi").fids("KSTL", 0, 1)
        self.assertEqual(aerodatabox.parse_fids(data), [])
        self.assertEqual(hdrs["x-ratelimit-api-units-remaining"], "388")

    def test_state_json_roundtrip_survives(self):
        s = new_state()
        poll(s, fids())
        json.loads(json.dumps(s))  # must be plain JSON: it is committed to the repo


if __name__ == "__main__":
    unittest.main()
