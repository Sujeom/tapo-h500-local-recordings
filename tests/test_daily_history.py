"""How many recordings a camera made on a given day, kept for good.

The hub's own index reaches back a day and its recordings about seventeen, so
"when was this camera last working properly?" had no answer here at all. It
was asked this week and could only be guessed at from memory.

A daily count goes into Home Assistant's long-term statistics and stays there,
so a camera that went dark on a Tuesday shows it as a column that stops. The
rolling 24-hour count beside it cannot do that job: it is never at rest, so
what it reads for a day depends on the minute it is read.
"""
import importlib
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

clips_mod = importlib.import_module("tapo_h500.clips")
sensor_mod = importlib.import_module("tapo_h500.sensor")
dt_util = sys.modules["homeassistant.util.dt"]

NOW = int(dt_util.utcnow().timestamp())
CAMERA = {"device_id": "cam0", "alias": "Front"}


def clip(start, length=15):
    return {"startTime": start, "endTime": start + length}


def describe(key):
    for description in sensor_mod.CAMERA_SENSORS:
        if description.key == key:
            return description
    raise AssertionError(f"no {key} sensor")


class LocalMidnight(unittest.TestCase):
    """Local, because "today" is a human word. The stub zone is -07:00, so a
    UTC boundary would put every evening on the wrong day."""

    def test_it_lands_on_the_start_of_the_local_day(self):
        moment = clips_mod.local_midnight(NOW)
        local = dt_util.as_local(dt_util.utc_from_timestamp(moment))
        self.assertEqual((local.hour, local.minute, local.second), (0, 0, 0))

    def test_it_is_not_in_the_future(self):
        self.assertLessEqual(clips_mod.local_midnight(NOW), NOW)

    def test_it_is_within_a_day(self):
        self.assertLess(NOW - clips_mod.local_midnight(NOW), 86400)

    def test_a_moment_just_after_midnight_belongs_to_the_new_day(self):
        """Somebody arriving home at 00:30 arrived on the new day. West of
        Greenwich a UTC boundary puts that back on the previous one."""
        local_zone = dt_util.LOCAL
        just_after = datetime(2026, 8, 29, 0, 30, tzinfo=local_zone)
        moment = int(just_after.timestamp())
        self.assertEqual(
            clips_mod.local_midnight(moment),
            int(just_after.replace(hour=0, minute=0).timestamp()))

    def test_the_evening_before_belongs_to_the_previous_day(self):
        local_zone = dt_util.LOCAL
        evening = datetime(2026, 8, 28, 23, 30, tzinfo=local_zone)
        morning = datetime(2026, 8, 29, 0, 30, tzinfo=local_zone)
        self.assertNotEqual(
            clips_mod.local_midnight(int(evening.timestamp())),
            clips_mod.local_midnight(int(morning.timestamp())))
        self.assertEqual(
            clips_mod.local_midnight(int(evening.timestamp())),
            int((morning - timedelta(days=1)).replace(
                hour=0, minute=0).timestamp()))


class TheDailyCount(unittest.TestCase):
    def _value(self, clips):
        coord, _ = harness._build()
        coord.clips_for = lambda index: list(clips)
        return describe("recordings_today").value(coord, 0, CAMERA)

    def test_it_counts_what_happened_today(self):
        midnight = clips_mod.local_midnight(NOW)
        self.assertEqual(
            self._value([clip(midnight + 60), clip(midnight + 3600),
                         clip(NOW - 60)]), 3)

    def test_yesterday_evening_does_not_count(self):
        """The whole reason it is a daily figure. A rolling window would
        carry last night into this morning's number."""
        midnight = clips_mod.local_midnight(NOW)
        self.assertEqual(self._value([clip(midnight - 1800)]), 0)

    def test_a_camera_that_recorded_nothing_reads_zero(self):
        """Not unknown. Zero is the finding, and a gap in the graph would
        read as a missing sensor rather than a dark camera."""
        self.assertEqual(self._value([]), 0)

    def test_the_recorder_sums_it_into_days(self):
        """Total-increasing is the shape Home Assistant already knows how to
        turn into a daily column. A measurement would give a mean."""
        self.assertIs(describe("recordings_today").state_class,
                      sensor_mod.SensorStateClass.TOTAL_INCREASING)

    def test_the_rolling_count_stays_a_measurement(self):
        """The two answer different questions and must not be merged."""
        self.assertIs(describe("recordings_24h").state_class,
                      sensor_mod.SensorStateClass.MEASUREMENT)

    def test_it_is_named(self):
        for name in ("translations/en.json", "strings.json"):
            with self.subTest(name):
                doc = json.loads((COMPONENT / name).read_text())
                self.assertIn("recordings_today", doc["entity"]["sensor"])


class TheZoneIsNeverCached(unittest.TestCase):
    """Home Assistant's date helpers are found once and kept. The zone they
    resolve is looked up every time.

    It can be changed in settings while Home Assistant is running, and a
    cached one would put every recording an hour out with nothing on screen
    to say why.
    """

    def setUp(self):
        self.original = dt_util.LOCAL
        self.addCleanup(setattr, dt_util, "LOCAL", self.original)

    def test_changing_the_zone_changes_the_answer_at_once(self):
        moment = int(datetime(2026, 8, 29, 12, 0,
                              tzinfo=timezone.utc).timestamp())
        clips_mod.local_hour(moment)          # warm whatever is kept
        dt_util.LOCAL = timezone(timedelta(hours=-7))
        west = clips_mod.local_hour(moment)
        dt_util.LOCAL = timezone(timedelta(hours=2))
        east = clips_mod.local_hour(moment)
        self.assertEqual((west, east), (5, 14))

    def test_the_date_follows_it_too(self):
        """Not only the hour: a zone change can move a recording to another
        day, which is what the daily count is built on."""
        late = int(datetime(2026, 8, 29, 23, 30,
                            tzinfo=timezone.utc).timestamp())
        dt_util.LOCAL = timezone(timedelta(hours=2))
        self.assertEqual(clips_mod.local_date(late), "2026-08-30")
        dt_util.LOCAL = timezone(timedelta(hours=-7))
        self.assertEqual(clips_mod.local_date(late), "2026-08-29")

    def test_the_helpers_are_kept_rather_than_looked_up_each_time(self):
        """A third of what the call costs, for something called once per
        clip, per sensor, per camera, per poll."""
        clips_mod.local_hour(0)
        self.assertIsNotNone(clips_mod._DT)
        self.assertIs(clips_mod._DT, dt_util)


if __name__ == "__main__":
    unittest.main()
