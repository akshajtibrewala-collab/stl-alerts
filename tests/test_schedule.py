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
        self.assertEqual(msg["title"], "Planned: Louisiana One (N8977G)")
        self.assertEqual(msg["body"], "Southwest WN 283 → MDW\nDep Mon 7:05 PM · Terminal 1 · Gate B12")
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
        self.assertEqual(msg["title"], "Swapped out: Louisiana One (N8977G)")
        self.assertEqual(msg["body"], "Southwest WN 283 → MDW\nDep Mon 7:05 PM · Terminal 1 · Gate B12 · now N4444X")
        self.assertEqual(msg["click"], "https://www.flightradar24.com/data/aircraft/N8977G")   # the plane that left
        self.assertEqual(poll(s, fids("N4444X"), NOW + timedelta(hours=6))[0], [])  # and then quiet

    def test_swap_in_standard_to_special(self):
        s = new_state()
        self.assertEqual(poll(s, fids("N4444X"))[0], [])  # standard tail: tracked silently
        alerts, _ = poll(s, fids("N8977G"), NOW + timedelta(hours=5))
        self.assertEqual([a["alert_type"] for a in alerts], ["swap_in"])
        msg = notify.build_schedule_message(alerts[0], 24, "u")
        self.assertEqual(msg["title"], "Swapped in: Louisiana One (N8977G)")
        self.assertTrue(msg["body"].endswith("replaces N4444X"))

    def test_special_to_different_special_is_one_combined_alert(self):
        s = new_state()
        poll(s, fids("N8977G"))
        alerts, _ = poll(s, fids("N1776R"), NOW + timedelta(hours=5))
        self.assertEqual([a["alert_type"] for a in alerts], ["swap_change"])
        msg = notify.build_schedule_message(alerts[0], 24, "u")
        self.assertEqual(msg["title"], "Swapped: Louisiana One → Independence One")
        self.assertTrue(msg["body"].endswith("N8977G → N1776R"))
        self.assertEqual(msg["click"], "https://www.flightradar24.com/data/aircraft/N1776R")

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


class CrossPassAlerts(unittest.TestCase):
    """A Planned alert (schedule) and a Live alert (ADS-B) are different alerts and must never block each other."""

    def test_planned_alert_does_not_suppress_the_schedule_pass_after_a_live_alert(self):
        s = new_state()
        st.record(s, {"registration": "N8977G", "callsign": "SWA283"}, NOW)   # ADS-B already alerted
        alerts, _ = poll(s, fids())
        self.assertEqual([a["alert_type"] for a in alerts], ["planned"])

    def test_planned_then_live_produces_two_distinct_alerts(self):
        import tempfile
        from unittest import mock
        from stlalerts import main as stmain
        real_now = st.now()
        sched_local = (real_now + timedelta(hours=2)).astimezone(timezone(timedelta(hours=-5)))
        board = fids(utc=(real_now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%MZ"),
                     local=sched_local.strftime("%Y-%m-%d %H:%M-05:00"))
        # ADS-B: the same tail, flight SWA283, now 20 nm south of STL heading north and descending
        live_ac = {"ac": [{"hex": "aa8977", "r": "N8977G", "flight": "SWA283 ", "t": "B38M", "lat": 38.42,
                           "lon": -90.37, "alt_baro": 3400, "baro_rate": -700, "track": 2}]}
        empty_ac = {"ac": []}
        tmp = Path(tempfile.mkdtemp())
        files = {n: tmp / f"{n}.json" for n in ("board", "live", "empty", "state")}
        files["board"].write_text(json.dumps(board))
        files["live"].write_text(json.dumps(live_ac))
        files["empty"].write_text(json.dumps(empty_ac))

        def run(adsb_file):
            sent = []
            args = type("A", (), dict(dry_run=False, fixture=str(files[adsb_file]), schedule_fixture=str(files["board"]),
                                      force_schedule=False, test_notify=False, state_file=str(files["state"])))()
            with mock.patch.dict("os.environ", {"NTFY_TOPIC": "t", "NTFY_SERVER": "https://ntfy.example"}),                     mock.patch.object(stmain.notify, "send", side_effect=lambda srv, topic, msg: sent.append(msg)),                     mock.patch.object(stmain.notify, "apply_control_messages", return_value=[]),                     mock.patch.object(stmain, "LEAD_CSV", tmp / "lead.csv"):
                stmain.run(args)
            return [m["title"] for m in sent]

        first = run("empty")    # schedule pass sees the tail; nothing airborne yet
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].startswith("Planned: Louisiana One"))
        second = run("live")    # ADS-B now sees the same tail in the air: must NOT be suppressed
        self.assertEqual(len(second), 1)
        self.assertTrue(second[0].startswith("Inbound: Louisiana One"))   # the live ADS-B alert, distinct from Planned
        self.assertEqual(run("live"), [])   # a true duplicate (same pass, same flight) stays deduped
        self.assertEqual(run("empty"), [])  # and the unchanged tail never re-sends the Planned alert

    def test_arrival_alert_shows_the_arrival_terminal_and_origin_arrow(self):
        """API 'movement' is the STL end of the flight, so an arrival's terminal is where it arrives at STL."""
        data = fids(direction="arrivals", number="AS 388", icao="ASA", utc="2026-09-22 00:10Z",
                    local="2026-09-21 19:10-05:00", terminal="1", gate="")
        data["arrivals"][0]["movement"]["airport"] = {"iata": "SEA"}      # the OTHER airport: where it came from
        data["arrivals"][0]["airline"] = {"name": "Alaska Airlines", "iata": "AS", "icao": "ASA"}
        f = aerodatabox.parse_fids(data)[0]
        self.assertEqual((f["direction"], f["terminal"], f["other_airport"]), ("Arrival", "1", "SEA"))
        by_reg, by_hex = liveries.alertable([row("N492AS", "UNCF", "Alaska Airlines")], {"active"}, {"livery"})
        data["arrivals"][0]["aircraft"]["reg"] = "N492AS"
        alerts = schedule.process(aerodatabox.parse_fids(data), new_state(), by_reg, by_hex, WATCH, NOW, SCFG, [])
        msg = notify.build_schedule_message(alerts[0], 24, "u")
        self.assertEqual(msg["title"], "Planned: UNCF (N492AS)")
        self.assertEqual(msg["body"], "Alaska AS 388 ← SEA\nArr Mon 7:10 PM · Terminal 1")

    def test_terminal_and_gate_are_omitted_when_missing(self):
        alerts, _ = poll(new_state(), fids(gate="", terminal=""))
        self.assertEqual(notify.build_schedule_message(alerts[0], 24, "u")["body"],
                         "Southwest WN 283 → MDW\nDep Mon 7:05 PM")

    def test_unverified_tag_and_diversion_tag_on_scheduled_alerts(self):
        unv = {"registration": "N8977G", "icao24": "", "airline": "Southwest Airlines", "aircraft_type": "B38M",
               "livery_name": "Louisiana One", "kind": "livery", "status": "unverified", "confidence": "best-effort"}
        by_reg, by_hex = liveries.alertable([unv], {"unverified"}, {"livery"})
        alerts = schedule.process(aerodatabox.parse_fids(fids(icao="QFA")), new_state(), by_reg, by_hex, WATCH, NOW, SCFG, [])
        body = notify.build_schedule_message(alerts[0], 24, "u")["body"]
        self.assertTrue(body.startswith("Southwest WN 283 → MDW · diversion?\n"))
        self.assertTrue(body.endswith("(unverified)"))

    def test_every_alert_type_opens_flightradar24_for_its_tail(self):
        base = {"alert_type": "planned", "registration": "N8977G", "livery_name": "L", "airline": "A",
                "flight_number": "WN 1", "direction": "Departure", "other_airport": "MDW", "scheduled": "x",
                "gate": "", "terminal": "", "tag": "routine", "status": "active", "confidence": "verified",
                "old_reg": "N1", "new_reg": "N8977G", "old_livery": "Old", "new_livery": "L"}
        for kind, tail in (("planned", "N8977G"), ("swap_in", "N8977G"), ("swap_change", "N8977G"), ("swap_out", "N1")):
            msg = notify.build_schedule_message({**base, "alert_type": kind}, 24, "u")
            self.assertEqual(msg["click"], f"https://www.flightradar24.com/data/aircraft/{tail}", kind)

    def test_click_and_actions_are_sent_as_ntfy_headers(self):
        from unittest import mock
        fake = mock.MagicMock()
        fake.__enter__.return_value.status = 200
        msg = {"title": "T", "body": "b", "tags": "airplane", "priority": "4",
               "click": "https://www.flightradar24.com/data/aircraft/N492AS", "actions": "http, Mute 24h, u"}
        with mock.patch("urllib.request.urlopen", return_value=fake) as uo:
            notify.send("https://ntfy.sh", "topic", msg)
        req = uo.call_args[0][0]
        self.assertEqual(req.get_header("Click"), "https://www.flightradar24.com/data/aircraft/N492AS")
        self.assertEqual(req.get_header("Actions"), "http, Mute 24h, u")

    def test_failed_send_is_rolled_back_and_retried(self):
        s = new_state()
        alerts, _ = poll(s, fids())
        schedule.undo(s, alerts[0])
        retry, _ = poll(s, fids(), NOW + timedelta(minutes=10))
        self.assertEqual([a["alert_type"] for a in retry], ["planned"])


def pend(state, now, k, hours=1.0):
    """k flights leaving `hours` from now that have no tail assigned yet."""
    state["flights"] = {f"X{i}|d|D": {"reg": None, "livery": None, "sched_utc": (now + timedelta(hours=hours)).isoformat()}
                        for i in range(k)}


class Polling(unittest.TestCase):
    DAY = datetime(2026, 9, 22, 11, 0, tzinfo=timezone.utc)   # 6 AM CDT: start of the operating day

    def served(self, at=None):
        """State whose last poll was at the start of the day."""
        s = new_state()
        schedule.record_poll(s, at or self.DAY, SCFG, "base")
        return s

    def test_first_run_polls_immediately(self):
        self.assertEqual(schedule.due(new_state(), self.DAY, SCFG), "base")

    def test_catch_poll_when_departing_flights_still_have_no_tail(self):
        s = self.served()
        now = self.DAY + timedelta(hours=2)
        pend(s, now, 6)                                     # 6 flights due within 3 h, no tail yet
        self.assertEqual(schedule.due(s, now, SCFG), "catch")

    def test_no_catch_poll_when_few_flights_are_waiting(self):
        s = self.served()
        now = self.DAY + timedelta(hours=2)
        pend(s, now, 2)                                     # below min_pending
        self.assertIsNone(schedule.due(s, now, SCFG))

    def test_flights_beyond_the_horizon_do_not_count_as_pending(self):
        s = self.served()
        now = self.DAY + timedelta(hours=2)
        pend(s, now, 20, hours=6)                           # lots of flights, but hours away
        self.assertIsNone(schedule.due(s, now, SCFG))

    def test_safety_poll_after_a_long_quiet_gap(self):
        s = self.served()
        self.assertEqual(schedule.due(s, self.DAY + timedelta(hours=4, minutes=30), SCFG), "base")

    def test_lull_saves_the_poll_for_when_the_bank_builds(self):
        s = self.served()
        self.assertIsNone(schedule.due(s, self.DAY + timedelta(hours=3, minutes=30), SCFG))   # lull: nothing waiting
        now = self.DAY + timedelta(hours=3, minutes=31)
        pend(s, now, 8)                                     # a departure bank appears: the saved poll is spent now
        self.assertEqual(schedule.due(s, now, SCFG), "catch")

    def test_min_gap_between_polls(self):
        s = self.served()
        now = self.DAY + timedelta(minutes=30)
        pend(s, now, 9)
        self.assertIsNone(schedule.due(s, now, SCFG))

    def test_no_polling_outside_the_operating_window(self):
        s = self.served()
        night = datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc)   # 1 AM CDT
        pend(s, night, 9)
        self.assertIsNone(schedule.due(s, night, SCFG))

    def test_a_day_of_polling_never_exceeds_the_allowance(self):
        s = new_state()
        n = None
        polls = 0
        t = self.DAY
        for _ in range(16 * 12):                            # every 5 minutes for the 16 h operating window
            pend(s, t, 9)                                   # worst case: flights always waiting
            if schedule.due(s, t, SCFG):
                n = schedule.plan(s, t, SCFG)[0]
                schedule.record_poll(s, t, SCFG, "catch")
                polls += 1
            t += timedelta(minutes=5)
        self.assertLessEqual(polls, n)
        self.assertGreaterEqual(polls, n - 1)               # and it really spends the allowance

    def test_polls_cluster_when_more_flights_are_waiting(self):
        """Same allowance, but polls land where flights are waiting, not on a fixed clock."""
        s = new_state()
        times = []
        t = self.DAY
        for _ in range(16 * 12):
            busy = 9 if 4 <= (t - self.DAY).total_seconds() / 3600 < 9 else 1   # a midday bank, quiet otherwise
            pend(s, t, busy)
            if schedule.due(s, t, SCFG):
                times.append(t)
                schedule.record_poll(s, t, SCFG, "catch")
            t += timedelta(minutes=5)
        inside = [x for x in times if 4 <= (x - self.DAY).total_seconds() / 3600 < 9]
        self.assertGreater(len(inside), len(times) / 2)

    def test_budget_exhaustion_stops_polling(self):
        s = self.served()
        s["adb"]["units"] = SCFG["monthly_budget_units"] - SCFG["budget_reserve_units"] - 1
        now = self.DAY + timedelta(hours=2)
        pend(s, now, 9)
        self.assertIsNone(schedule.due(s, now, SCFG))

    def test_backoff_blocks_until_expiry(self):
        s = new_state()
        schedule.block(s, NOW, 2)
        self.assertIsNone(schedule.due(s, NOW + timedelta(hours=1), SCFG))
        self.assertEqual(schedule.due(s, NOW + timedelta(hours=3), SCFG), "base")

    def test_swap_watch_poll_when_a_known_special_flight_is_near_but_capped(self):
        s = self.served()
        now = self.DAY + timedelta(hours=2)
        s["flights"]["SWA283|d|D"] = {"reg": "N8977G", "livery": "Louisiana One",
                                      "sched_utc": (now + timedelta(hours=1)).isoformat()}
        self.assertEqual(schedule.due(s, now, SCFG), "watch")
        for _ in range(SCFG["max_extra_calls_per_day"]):
            schedule.record_poll(s, now, SCFG, "watch")
        self.assertNotEqual(schedule.due(s, now + timedelta(minutes=100), SCFG), "watch")

    def test_far_off_special_flight_does_not_trigger_a_watch_poll(self):
        s = self.served()
        now = self.DAY + timedelta(hours=2)
        s["flights"]["SWA283|d|D"] = {"reg": "N8977G", "livery": "Louisiana One",
                                      "sched_utc": (now + timedelta(hours=9)).isoformat()}
        self.assertIsNone(schedule.due(s, now, SCFG))

    def test_daily_poll_count_follows_remaining_budget(self):
        s = new_state()
        n, calls_left, days_left = schedule.plan(s, datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc), SCFG)
        self.assertEqual((n, calls_left, days_left), (6, 190, 31))
        low = new_state()
        schedule.budget(low, self.DAY, SCFG)
        low["adb"]["units"] = 370          # only 5 calls left with 9 days to go: under one a day
        self.assertEqual(schedule.plan(low, self.DAY, SCFG)[0], 0)
        low["adb"]["last_poll"] = self.DAY.isoformat()
        pend(low, self.DAY + timedelta(hours=8), 9)
        self.assertIsNone(schedule.due(low, self.DAY + timedelta(hours=8), SCFG))

    def test_a_whole_month_never_exceeds_the_budget_even_if_flights_are_always_waiting(self):
        s = new_state()
        cap = SCFG["monthly_budget_units"] - SCFG["budget_reserve_units"]
        peak = 0
        for day in range(1, 31):
            t = datetime(2026, 9, day, 11, 0, tzinfo=timezone.utc)
            for _ in range(15 * 6):                         # every 10 minutes, staying inside the calendar day
                pend(s, t, 9)
                if schedule.due(s, t, SCFG):
                    schedule.record_poll(s, t, SCFG, "catch")
                    peak = max(peak, s["adb"]["units"])
                t += timedelta(minutes=10)
        self.assertLessEqual(peak, cap)
        self.assertGreaterEqual(peak, cap - 30)            # and almost all of it gets used

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
