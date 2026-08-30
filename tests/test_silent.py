"""A camera that has stopped working looks exactly like a quiet one.

The hub offers nothing to check: 16 fields in the paired-device record and not
one is an online flag, a signal strength or a battery, and all eleven battery
methods answer -40106. So silence is the only evidence there is, and these
tests are mostly about the two ways of getting that wrong -- calling every
camera silent before the first poll has happened, and claiming to know about
a period longer than the hub was ever asked about.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
BINARY_SENSOR = (COMPONENT / "binary_sensor.py").read_text()
REPAIRS = (COMPONENT / "repairs.py").read_text()
CONFIG_FLOW = (COMPONENT / "config_flow.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
binary_sensor_mod = importlib.import_module("tapo_h500.binary_sensor")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
CAMERA = {"device_id": "cam0", "alias": "Front"}


def clip(start, length=15):
    return {"startTime": start, "endTime": start + length}


class _Client:
    def __init__(self):
        self.clips = []

    def cameras(self):
        return [{"device_id": "cam0", "alias": "Front"},
                {"device_id": "cam1", "alias": "Back Gate"}]

    def recent(self, camera, start, end):
        return list(self.clips) if camera["device_id"] == "cam0" else []

    def detections(self, camera, start, end):
        return []

    def hub_status(self):
        return {}


def build(**options):
    client = _Client()
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20, **options), client)
    coord._download_new = lambda *a, **k: None
    return coord, client


def poll(coord):
    coord.data = asyncio.run(coord._async_update_data())


class BeforeTheFirstPoll(unittest.TestCase):
    def test_nothing_is_known_yet(self):
        """An empty list before the hub has been asked means "not asked",
        not "nothing happened". Reporting every camera silent on startup
        would be an alarm about the integration, not the hardware."""
        coord, _ = build()
        self.assertIsNone(coord.silent_seconds(0))

    def test_and_so_no_camera_is_reported(self):
        coord, client = build()
        # The paired list is fetched at the top of the first poll, before any
        # recordings are. That window -- cameras known, activity not -- is
        # where "silent" must still mean "not yet known".
        coord.cameras = client.cameras()
        self.assertEqual(coord.silent_cameras(3600), [])


class AfterAPoll(unittest.TestCase):
    def test_a_camera_with_recent_activity_is_not_silent(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertLess(coord.silent_seconds(0), 3600)

    def test_a_camera_with_nothing_at_all_reports_the_whole_window(self):
        """Not more. The hub was asked about a day; anything longer would be
        invented, and this is the number a threshold is compared against."""
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertEqual(coord.silent_seconds(1), const.LOOKBACK_SECONDS)

    def test_it_names_only_the_quiet_cameras(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertEqual(coord.silent_cameras(3600), ["Back Gate"])

    def test_a_higher_threshold_forgives_a_quiet_camera(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 7200, "endTime": NOW - 7185}]
        poll(coord)
        self.assertEqual(coord.silent_cameras(3600), ["Front", "Back Gate"])
        self.assertEqual(coord.silent_cameras(const.LOOKBACK_SECONDS),
                         ["Back Gate"])

    def test_a_clock_ahead_of_the_hub_does_not_go_negative(self):
        """A recording stamped in the future would otherwise produce a
        negative silence, which reads as "silent for -40 seconds"."""
        coord, client = build()
        client.clips = [{"startTime": NOW + 300, "endTime": NOW + 315}]
        poll(coord)
        self.assertEqual(coord.silent_seconds(0), 0)


class Threshold(unittest.TestCase):
    def test_the_option_cannot_exceed_the_poll_window(self):
        """The form caps it, but an entry saved before the cap existed could
        hold anything, and a threshold past the window makes a sensor that
        never turns on for a reason nobody can see."""
        body = BINARY_SENSOR.split("def silent_threshold", 1)[1].split(
            "\n\nclass ", 1)[0]
        self.assertIn("LOOKBACK_SECONDS", body)
        self.assertIn("min(", body)

    def test_the_form_offers_no_more_than_the_window(self):
        settings = CONFIG_FLOW.split("async_step_settings", 1)[1]
        self.assertIn("LOOKBACK_SECONDS // 3600", settings)

    def test_the_option_is_on_the_settings_form(self):
        settings = CONFIG_FLOW.split("async_step_settings", 1)[1]
        self.assertIn("CONF_SILENT_HOURS", settings)


class Entity(unittest.TestCase):
    def test_every_camera_gets_one(self):
        setup = BINARY_SENSOR.split("async_setup_entry", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn("H500CameraSilent(coordinator, index, camera)", setup)

    def test_it_is_unknown_rather_than_fine_before_the_first_poll(self):
        """None reads as "unknown" in the frontend, which is the truth.
        False would say "fine" about a camera nobody has asked about yet."""
        coord, _ = build()
        sensor = binary_sensor_mod.H500CameraSilent(coord, 0, CAMERA)
        self.assertIsNone(sensor.is_on)


class Repair(unittest.TestCase):
    def test_it_raises_an_issue(self):
        self.assertIn("SILENT_CAMERA_ISSUE", REPAIRS)
        self.assertIn("_silent_cameras(hass, entry_id, coordinator)", REPAIRS)

    def test_the_issue_clears_when_the_camera_comes_back(self):
        """An issue that never clears is worse than none."""
        body = REPAIRS.split("def _silent_cameras", 1)[1].split(
            "\n\ndef ", 1)[0]
        self.assertIn("async_delete_issue", body)


clips_mod = importlib.import_module("tapo_h500.clips")


def at_local_hour(day: int, hour: int, minute: int = 0) -> int:
    """An epoch on local day `day` (0 = the day containing NOW) at hour:minute.

    The harness pins local time to -07:00, deliberately not UTC, so a
    function that walks hours in the wrong zone lands on the wrong rates.
    """
    base = NOW - (NOW % 86400)              # a UTC midnight anchor
    # hour+7 may pass 24 and spill into the next UTC day; that is what
    # keeps 10pm local on day N ordered before 6am local on day N+1.
    return base + day * 86400 + (hour + 7) * 3600 + minute * 60


class ExpectedSince(unittest.TestCase):
    """How many events history predicted during the current silence.

    A fixed 24-hour threshold called yesterday's dead cameras healthy for
    half a day: the busy doorbell does ~25 clips a day, and 14.5 silent
    hours on it was screaming long before any fixed number would listen. A
    plain "typical gap" misfires the other way -- every camera is naturally
    silent overnight. So the question is not "how long has it been quiet"
    but "how much history says should have happened by now": silence across
    dead hours accrues nothing, silence across busy hours accrues fast.
    """

    def _day_of_activity(self, day: int, hours=range(12, 16)):
        return [{"startTime": at_local_hour(day, h),
                 "endTime": at_local_hour(day, h) + 15} for h in hours]

    def test_overnight_silence_predicts_nothing(self):
        """The camera that records nothing between 10pm and 6am every day is
        not broken at 3am."""
        clips = self._day_of_activity(0)
        since = at_local_hour(0, 22)
        now = at_local_hour(1, 6)
        self.assertLess(clips_mod.expected_since(
            clips, since, now, 86400), 0.5)

    def test_silence_across_the_busy_hours_accrues(self):
        """Four active hours crossed at one event each: about four expected."""
        clips = self._day_of_activity(0)
        since = at_local_hour(0, 16)
        now = at_local_hour(1, 16)
        expected = clips_mod.expected_since(clips, since, now, 86400)
        self.assertGreaterEqual(expected, 3.5)
        self.assertLessEqual(expected, 4.5)

    def test_a_partial_hour_counts_its_fraction(self):
        clips = [{"startTime": at_local_hour(0, 12, m), "endTime": 0}
                 for m in (5, 25, 45, 55)]  # rate 4 in the noon hour
        since = at_local_hour(0, 12)
        half = clips_mod.expected_since(
            clips, since, since + 1800, 86400)
        self.assertAlmostEqual(half, 2.0, delta=0.1)

    def test_two_days_of_history_halve_the_rate(self):
        """Counts are per-day rates, not raw totals: the same clips over a
        two-day window predict half as much per crossed hour."""
        clips = self._day_of_activity(0)
        since = at_local_hour(0, 16)
        now = at_local_hour(1, 16)
        one_day = clips_mod.expected_since(clips, since, now, 86400)
        two_day = clips_mod.expected_since(clips, since, now, 2 * 86400)
        self.assertAlmostEqual(two_day, one_day / 2, delta=0.2)

    def test_a_zero_window_predicts_nothing_rather_than_dividing_by_it(self):
        """The boundary of the guard, not just one side of it.

        `window` becomes the divisor two lines later (`days = window / 86400`),
        so the guard is what stands between a zero window and a
        ZeroDivisionError inside a poll. Mutation testing found this: widening
        `window <= 0` to `window < 0` survived the whole suite, because every
        test passed a real window and nothing exercised nought.
        """
        clips = [{"startTime": at_local_hour(0, 9), "endTime": 0}]
        self.assertEqual(clips_mod.expected_since(clips, 0, 100, 0), 0.0)
        self.assertEqual(clips_mod.expected_since(clips, 0, 100, -1), 0.0)

    def test_no_history_predicts_nothing(self):
        self.assertEqual(clips_mod.expected_since([], NOW - 3600, NOW, 86400), 0.0)

    def test_time_running_backwards_predicts_nothing(self):
        clips = self._day_of_activity(0)
        self.assertEqual(clips_mod.expected_since(
            clips, NOW, NOW - 3600, 86400), 0.0)

    def test_yesterdays_outage_is_caught_in_hours_not_a_day(self):
        """The real case, in miniature: a camera doing ~25 a day across the
        waking hours goes dark in the evening. By the next morning the
        expected count is far past any sensible alarm line -- the fixed
        24-hour rule was still hours from noticing."""
        clips = [{"startTime": at_local_hour(0, h, m), "endTime": 0}
                 for h in range(8, 21) for m in (0, 30)][:25]
        since = at_local_hour(0, 20, 35)
        by_morning = clips_mod.expected_since(
            clips, since, at_local_hour(1, 11), 86400)
        self.assertGreaterEqual(by_morning, const.SILENT_EXPECTED)


class AdaptiveSensorWiring(unittest.TestCase):
    """The sensor asks both questions: the ceiling and the expectation.

    The configured hours stay as a hard ceiling -- a camera with no history
    at all can never accrue an expectation, and silence past the option must
    flag exactly as it always has. The adaptive half only ever flags EARLIER.
    """

    BODY = BINARY_SENSOR.split("class H500CameraSilent", 1)[1].split(
        "\nclass ", 1)[0]

    def _sensor(self, clips, **options):
        coord, _ = build(**options)
        coord._primed = True
        coord.clips_for = lambda index: list(clips)
        return binary_sensor_mod.H500CameraSilent(coord, 0, CAMERA), coord

    def test_a_busy_camera_is_flagged_long_before_the_ceiling(self):
        """Two recordings an hour for a day, then four hours of nothing. The
        ceiling is twenty-four hours away and this is already wrong."""
        clips = [clip(NOW - 4 * 3600 - n * 1800) for n in range(48)]
        sensor, coord = self._sensor(clips, silent_hours=24)
        self.assertLess(coord.silent_seconds(0), 24 * 3600)
        self.assertTrue(sensor.is_on)

    def test_the_ceiling_still_catches_a_camera_with_no_history(self):
        """Nothing to build an expectation from, so only the hard limit can
        fire -- and it must."""
        sensor, coord = self._sensor([])
        self.assertEqual(binary_sensor_mod.expected_events(coord, 0), 0.0)
        self.assertTrue(sensor.is_on)

    def test_a_camera_recording_normally_is_flagged_on_neither(self):
        sensor, _ = self._sensor([clip(NOW - 60 * n) for n in range(1, 40)])
        self.assertFalse(sensor.is_on)

    def test_the_expectation_is_shown_alongside_the_silence(self):
        clips = [clip(NOW - 4 * 3600 - n * 1800) for n in range(48)]
        sensor, _ = self._sensor(clips)
        attributes = sensor.extra_state_attributes
        self.assertGreater(attributes["expected_events"], 0)
        self.assertGreater(attributes["silent_seconds"], 0)


class TheAlarmDoesNotSwitchItselfOff(unittest.TestCase):
    """A watchdog whose evidence expires is a watchdog that lies.

    `expected_since` draws its baseline from the clips still inside the poll
    window. While a camera is dark those clips age out, so the expectation
    climbs, peaks, and then falls back through its own alarm line with the
    camera still dead. The ceiling catches it eventually; between the two the
    sensor reads healthy, which is worse than never having fired.
    """

    # The Side Doorbell's real histogram, measured off the hub during the
    # 2026-08-25 outage: recordings per local hour across the two days before
    # it went dark. Clustered, not sprinkled -- that is what makes the rate
    # collapse as the window scrolls, and a uniform stand-in does not
    # reproduce the bug at all.
    SHAPE = {0: {14: 1, 17: 1, 19: 6, 20: 3, 22: 2, 23: 6},
             1: {0: 4, 1: 1, 10: 1, 12: 1, 13: 2, 14: 3}}

    @classmethod
    def _real_shape(cls) -> tuple[list[dict], int]:
        clips = [{"startTime": at_local_hour(day, hour, n * 7), "endTime": 0}
                 for day, hours in cls.SHAPE.items()
                 for hour, count in hours.items()
                 for n in range(count)]
        return clips, max(c["startTime"] for c in clips)

    def _score_at(self, clips, last, hours) -> float:
        """What the adaptive half computes `hours` into the outage, with the
        window aged exactly as the coordinator would age it."""
        now = last + hours * 3600
        indexed = [c for c in clips
                   if c["startTime"] >= now - const.LOOKBACK_SECONDS]
        if not indexed:
            return 0.0
        return clips_mod.expected_since(
            indexed, last, now, const.LOOKBACK_SECONDS)

    def test_the_expectation_decays_to_nothing_while_the_camera_is_dead(self):
        """The bug this class exists for, shown rather than asserted.

        Past the poll window there is no history left to measure against, so
        the adaptive half scores zero at the exact point the camera is most
        certainly dead. The existing coverage missed it by passing the same
        clip list at every `now`, which is the one thing the real coordinator
        never does.
        """
        clips, last = self._real_shape()
        peak = max(self._score_at(clips, last, h) for h in range(1, 25))
        self.assertGreater(peak, 0.0, "it should score something while dark")
        self.assertEqual(self._score_at(clips, last, 36), 0.0)
        self.assertLess(self._score_at(clips, last, 36), peak,
                        "a longer outage must never score lower than a shorter one")

    def test_the_latch_is_what_keeps_it_on_through_the_decay(self):
        """End to end, at the level this harness can reach.

        The sensor computes a verdict and hands it to latch_silent. So feed
        the REAL verdict sequence -- recomputed hour by hour against a window
        that ages exactly as the coordinator ages it -- and show two things:
        the raw verdict really does fall back to False while the camera is
        still dark, and the latched one does not. Without the second half
        this test would pass against the unlatched code and prove nothing.
        """
        clips, last = self._real_shape()
        raw, latched = [], []
        coord, client = build()
        client.clips = [{"startTime": last, "endTime": last + 15}]
        poll(coord)
        for hours in range(6, 25, 3):
            verdict = self._score_at(clips, last, hours) >= const.SILENT_EXPECTED
            raw.append(verdict)
            latched.append(coord.latch_silent(0, verdict))
        self.assertIn(True, raw, "the outage must trip at some point")
        self.assertIn(False, raw[raw.index(True):],
                      "and the raw verdict must fall back -- that IS the bug")
        held = latched[raw.index(True):]
        self.assertTrue(all(held),
                        f"the latch must hold once tripped, got {latched}")

    def test_a_tripped_alarm_is_held_while_the_camera_stays_dark(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertTrue(coord.latch_silent(0, True))     # trips
        # ...and now the expectation decays away underneath it.
        self.assertTrue(coord.latch_silent(0, False))
        self.assertTrue(coord.latch_silent(0, False))
        self.assertTrue(coord.silent_latched(0))

    def test_only_a_new_recording_clears_it(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        coord.latch_silent(0, True)
        self.assertTrue(coord.latch_silent(0, False))
        # The camera records again: the one thing that counts as recovery.
        client.clips = [{"startTime": NOW + 600, "endTime": NOW + 615}]
        poll(coord)
        self.assertFalse(coord.latch_silent(0, False))
        self.assertFalse(coord.silent_latched(0))

    def test_a_camera_that_never_recorded_stays_held(self):
        """last_activity is None for a camera dark longer than the window.
        None must not read as "it recorded something new"."""
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertIsNone(coord.last_activity(1))
        self.assertTrue(coord.latch_silent(1, True))
        self.assertTrue(coord.latch_silent(1, False))

    def test_an_untripped_camera_is_not_latched_by_asking(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertFalse(coord.latch_silent(0, False))
        self.assertFalse(coord.silent_latched(0))

    def test_the_sensor_says_when_it_is_being_held(self):
        """"Still on, and here is why" -- otherwise a held alarm reads as a
        sensor stuck for no reason anybody can see."""
        coord, _ = build()
        coord._primed = True
        coord.clips_for = lambda index: []
        sensor = binary_sensor_mod.H500CameraSilent(coord, 0, CAMERA)
        self.assertTrue(sensor.is_on)
        self.assertTrue(coord.silent_latched(0))
        self.assertTrue(
            sensor.extra_state_attributes["held_since_last_recording"])

    def test_a_recording_clears_the_latch(self):
        """Only the camera producing again counts as recovery. The
        expectation falling back under its line is the decay this ignores."""
        coord, _ = build()
        coord._primed = True
        coord.clips_for = lambda index: []
        sensor = binary_sensor_mod.H500CameraSilent(coord, 0, CAMERA)
        self.assertTrue(sensor.is_on)
        coord.clips_for = lambda index: [clip(NOW - 30)]
        self.assertFalse(sensor.is_on)
        self.assertFalse(coord.silent_latched(0))


if __name__ == "__main__":
    unittest.main()
