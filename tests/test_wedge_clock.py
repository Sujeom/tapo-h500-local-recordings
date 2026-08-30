"""How often this hub stops serving, answerable months later.

The hub stops serving video every so often and keeps no record of having done
it. Neither does Home Assistant in any lasting form: the wedge binary sensor
says whether it is happening now, and binary sensors get no long-term
statistics, so its history ends at the recorder's purge -- ten days, where the
question worth asking is whether this hub is getting worse over months.

Numbers do get kept forever. So the same fact is also a number: hours the
media path has been serving, climbing while it does and zero while it does
not. The long-term graph is a sawtooth whose peaks are the times to wedge and
whose resets are the wedges themselves.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

const = importlib.import_module("tapo_h500.const")
sensor_mod = importlib.import_module("tapo_h500.sensor")
dt_util = sys.modules["homeassistant.util.dt"]
NOW = dt_util.utcnow().timestamp()

HOUR = 3600


def wedge(at, tried=(), ended=None):
    return {"at": at, "tried": list(tried), "ended": ended}


class TheRecord(unittest.TestCase):
    def setUp(self):
        self.coord, _ = harness._build()

    def _wedge(self, at):
        """Drive a wedge on and off through the real transitions."""
        self.coord.media._healthy_since = at - 0  # left where the caller put it
        self.coord.note_media_status("wedged")

    def test_a_fresh_start_is_healthy_and_counts_from_now(self):
        self.assertEqual(self.coord.healthy_seconds, 0.0)
        self.assertEqual(self.coord.wedges, [])
        self.assertFalse(self.coord.media_wedged)

    def test_the_clock_climbs_while_the_hub_serves(self):
        self.coord.media._healthy_since = NOW - 5 * HOUR
        self.assertAlmostEqual(self.coord.healthy_seconds, 5 * HOUR, places=3)

    def test_it_is_zero_while_wedged(self):
        self.coord.media._healthy_since = NOW - 5 * HOUR
        self.coord.note_media_status("wedged")
        self.assertEqual(self.coord.healthy_seconds, 0.0)

    def test_a_wedge_is_recorded_once_not_per_poll(self):
        for _ in range(5):
            self.coord.note_media_status("wedged")
        self.assertEqual(len(self.coord.wedges), 1)

    def test_recovery_restarts_the_clock(self):
        self.coord.media._healthy_since = NOW - 5 * HOUR
        self.coord.note_media_status("wedged")
        self.coord.note_media_status("healthy")
        self.assertEqual(self.coord.healthy_seconds, 0.0)
        self.assertEqual(len(self.coord.wedges), 1)

    def test_a_second_wedge_is_a_second_entry(self):
        self.coord.note_media_status("wedged")
        self.coord.note_media_status("healthy")
        self.coord.note_media_status("wedged")
        self.assertEqual(len(self.coord.wedges), 2)

    def test_empty_downloads_are_a_wedge_too(self):
        """The sentinel may never have run. The downloads are their own
        evidence, and that is the outage people actually notice."""
        self.coord.note_empty_download()
        self.assertEqual(self.coord.wedges, [])
        self.coord.note_empty_download()
        self.assertEqual(len(self.coord.wedges), 1)
        self.assertTrue(self.coord.media_wedged)

    def test_a_served_download_ends_it(self):
        """And the record has to notice, not just the counter.

        Recovery is where the clock restarts. A recovery the record slept
        through leaves it believing the hub is still wedged, so the next
        outage is not a change of state and goes unrecorded -- the log stops
        at one entry and stays there.
        """
        self.coord.media._healthy_since = NOW - 9 * HOUR
        self.coord.note_empty_download()
        self.coord.note_empty_download()
        self.coord.note_served_download()
        self.assertFalse(self.coord.media_wedged)
        self.assertEqual(len(self.coord.wedges), 1)
        self.assertEqual(self.coord.healthy_seconds, 0.0,
                         "the clock restarts at recovery")
        self.coord.note_media_status("wedged")
        self.assertEqual(len(self.coord.wedges), 2,
                         "the next outage is still a change of state")

    def test_the_two_signals_do_not_double_count(self):
        """A hub that is serving empty AND failing its handshake is one
        outage, not two."""
        self.coord.note_empty_download()
        self.coord.note_empty_download()
        self.coord.note_media_status("wedged")
        self.assertEqual(len(self.coord.wedges), 1)

    def test_the_longest_run_survives_the_wedge_that_ended_it(self):
        self.coord.media._healthy_since = NOW - 12 * HOUR
        self.coord.note_media_status("wedged")
        self.coord.note_media_status("healthy")
        self.assertAlmostEqual(self.coord.longest_healthy_seconds,
                               12 * HOUR, places=3)

    def test_the_run_in_progress_can_be_the_longest(self):
        self.coord.media._healthy_since = NOW - 20 * HOUR
        self.assertAlmostEqual(self.coord.longest_healthy_seconds,
                               20 * HOUR, places=3)

    def test_counts_are_windowed(self):
        self.coord.media.wedges = [wedge(NOW - 8 * 86400), wedge(NOW - 3 * 86400),
                             wedge(NOW - 3600)]
        self.assertEqual(self.coord.wedges_since(7 * 86400), 2)
        self.assertEqual(self.coord.wedges_since(86400), 1)

    def test_the_log_does_not_grow_without_end(self):
        """Ninety days of onsets is evidence; a year of them is a leak."""
        self.coord.media.wedges = [wedge(NOW - const.WEDGE_HISTORY_SECONDS - 1),
                             wedge(NOW - 86400)]
        self.coord.note_media_status("wedged")
        self.assertEqual(len(self.coord.wedges), 2,
                         "the stale one goes, the recent one and the new "
                         "one stay")


class TheSensor(unittest.TestCase):
    def _sensor(self):
        coord, _ = harness._build()
        return coord, sensor_mod.H500WedgeClock(coord, harness._Entry(20))

    def test_it_reports_hours_not_seconds(self):
        """Twelve hours between wedges is the observed gap; a seconds axis
        would be unreadable at the span that matters."""
        coord, sensor = self._sensor()
        coord.media._healthy_since = NOW - 90 * 60
        self.assertAlmostEqual(sensor.native_value, 1.5, places=3)

    def test_the_recorder_keeps_it_forever(self):
        """A measurement state class is the whole reason this exists as a
        number rather than only as the binary sensor beside it."""
        _, sensor = self._sensor()
        self.assertIs(sensor._attr_state_class,
                      sensor_mod.SensorStateClass.MEASUREMENT)
        self.assertIs(sensor._attr_native_unit_of_measurement,
                      sensor_mod.UnitOfTime.HOURS)
        self.assertIs(sensor._attr_device_class,
                      sensor_mod.SensorDeviceClass.DURATION)

    def test_the_attributes_carry_the_counts(self):
        coord, sensor = self._sensor()
        coord.media.wedges = [wedge(NOW - 8 * 86400), wedge(NOW - 2 * 86400),
                        wedge(NOW - 600)]
        coord.media._longest_healthy = 30 * HOUR
        attributes = sensor.extra_state_attributes
        self.assertEqual(attributes["wedges_7d"], 2)
        self.assertEqual(attributes["wedges_24h"], 1)
        self.assertEqual(attributes["longest_healthy_hours"], 30.0)
        self.assertTrue(attributes["last_wedge"].startswith("20"))

    def test_last_wedge_is_none_when_there_has_not_been_one(self):
        """Not an epoch date, which would read as "wedged in 1970"."""
        _, sensor = self._sensor()
        self.assertIsNone(sensor.extra_state_attributes["last_wedge"])

    def test_it_is_registered(self):
        self.assertIn("H500WedgeClock(coordinator, entry)",
                      (COMPONENT / "sensor.py").read_text())

    def test_it_is_named(self):
        for name in ("translations/en.json", "strings.json"):
            with self.subTest(name):
                doc = json.loads((COMPONENT / name).read_text())
                self.assertIn("media_healthy_for",
                              doc["entity"]["sensor"])


if __name__ == "__main__":
    unittest.main()


class TheRecoveryLog(unittest.TestCase):
    """Every outage used to start the diagnosis from nothing.

    The record said an outage happened and never what was done about it, so
    "does restarting this hub actually help?" stayed a matter of memory. An
    attempt now hangs on the episode it was made against, beside when that
    episode ended.
    """

    def setUp(self):
        self.coord, _ = harness._build()

    def _wedge(self):
        self.coord.note_media_status("wedged")

    def test_an_attempt_hangs_on_the_outage_it_was_made_against(self):
        self._wedge()
        self.coord.note_recovery_attempt("hub restart")
        self.assertEqual(
            [a["what"] for a in self.coord.wedges[-1]["tried"]],
            ["hub restart"])

    def test_an_attempt_while_healthy_invents_no_outage(self):
        """A record of an outage that never happened is worse than no record
        of the attempt."""
        self.coord.note_recovery_attempt("hub restart")
        self.assertEqual(self.coord.wedges, [])

    def test_an_attempt_after_recovery_does_not_reopen_the_last_one(self):
        self._wedge()
        self.coord.note_media_status("healthy")
        self.coord.note_recovery_attempt("hub restart")
        self.assertEqual(self.coord.wedges[-1]["tried"], [])

    def test_recovery_closes_the_episode(self):
        self._wedge()
        self.coord.note_media_status("healthy")
        self.assertIsNotNone(self.coord.wedges[-1]["ended"])

    def test_the_restart_path_does_not_claim_the_hub_came_back(self):
        """It borrowed the served-download notification to reset its
        counters. That one means bytes arrived: it would clear the frame
        marks, retry the failed clips, and close the outage -- reporting a
        cure at the moment the cure was only being attempted.
        """
        self.coord.note_empty_download()
        self.coord.note_empty_download()
        self.assertEqual(len(self.coord.wedges), 1)
        self.coord.note_restarting()
        self.assertIsNone(self.coord.wedges[-1]["ended"])
        self.assertEqual(
            [a["what"] for a in self.coord.wedges[-1]["tried"]],
            ["hub restart"])
        self.assertEqual(self.coord.media._empty, 0,
                         "it still starts counting fresh")

    def test_a_restart_that_did_not_work_reads_differently_from_one_that_did(self):
        self._wedge()
        self.coord.note_restarting()
        self.coord.note_media_status("healthy")
        cured = self.coord.recovery_log()[0]
        self._wedge()
        self.coord.note_restarting()
        still_broken = self.coord.recovery_log()[0]
        self.assertIsNotNone(cured["lasted_minutes"])
        self.assertIsNone(still_broken["lasted_minutes"])
        self.assertEqual(len(self.coord.recovery_log()), 2)

    def test_the_log_reads_as_times_and_minutes(self):
        """The audience is somebody reading a diagnostics file or a support
        thread, not code."""
        self._wedge()
        self.coord.note_recovery_attempt("player id rotated")
        entry = self.coord.recovery_log()[0]
        self.assertTrue(entry["at"].startswith("20"))
        self.assertEqual(entry["tried"][0]["what"], "player id rotated")
        self.assertIsInstance(entry["tried"][0]["after_minutes"], float)

    def test_it_is_newest_first_and_bounded(self):
        for _ in range(12):
            self._wedge()
            self.coord.note_media_status("healthy")
        log = self.coord.recovery_log(limit=5)
        self.assertEqual(len(log), 5)
        self.assertGreaterEqual(log[0]["at"], log[-1]["at"])

    def test_diagnostics_carry_it(self):
        source = (COMPONENT / "diagnostics.py").read_text()
        self.assertIn("coordinator.recovery_log()", source)
