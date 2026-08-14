"""The shape of a day, and how many people were actually in it.

`recordings_24h` answers a question nobody asked. The hub reports moments, not
presence, so one visitor waiting four minutes at the door files sixteen
recordings -- a day reading "48 recordings" and a day reading "3 visits" can be
the same day, and only one of those numbers means anything to a person.

The helpers are run for real; the entity is checked statically, the way every
other platform here is, because sensor.py imports the Home Assistant entity
platform.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "sensor.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
HOUR = 3600


def clip(when, seconds=15):
    return {"startTime": when, "endTime": when + seconds}


class HourlyShape(unittest.TestCase):
    def test_it_returns_a_slot_for_every_hour(self):
        """A card draws this straight, so a short list is a broken chart."""
        self.assertEqual(len(clips.hourly_counts([])), 24)
        self.assertEqual(sum(clips.hourly_counts([])), 0)

    def test_recordings_land_in_their_own_hour(self):
        counts = clips.hourly_counts([clip(NOW), clip(NOW), clip(NOW - 3 * HOUR)])
        busy = [hour for hour, count in enumerate(counts) if count]
        self.assertEqual(len(busy), 2)
        self.assertEqual(sum(counts), 3)

    def test_the_hours_are_local_not_utc(self):
        """NOW is 05:46 UTC and 22:46 in the harness's -07:00 zone. An
        hour-of-day chart in the wrong zone is not slightly wrong, it is a
        chart of somebody else's day."""
        counts = clips.hourly_counts([clip(NOW)])
        self.assertEqual(counts.index(1), 22)

    def test_a_recording_with_no_start_is_dropped(self):
        self.assertEqual(sum(clips.hourly_counts([{"endTime": NOW}])), 0)

    def test_the_peak_agrees_with_the_shape(self):
        """busiest_hour is this reduced to its maximum; the two disagreeing
        would mean two answers to one question."""
        run = [clip(NOW - 3 * HOUR), clip(NOW - 3 * HOUR), clip(NOW)]
        counts = clips.hourly_counts(run)
        self.assertEqual(clips.busiest_hour(run), counts.index(max(counts)))


class Visits(unittest.TestCase):
    def test_a_run_of_clips_is_one_visit(self):
        run = [clip(NOW - 240 + step * 15) for step in range(16)]
        self.assertEqual(len(clips.sessions(run, const.LOITER_GAP)), 1)

    def test_the_longest_is_measured_first_to_last(self):
        """Not to now. A single fifteen-second clip is evidence of fifteen
        seconds, and counting the silence since would inflate every brief
        visit the moment it ended."""
        run = [clip(NOW - 300), clip(NOW - 240)]
        self.assertEqual(clips.longest_visit(run, const.LOITER_GAP), 75)

    def test_the_longest_is_the_longest_not_the_latest(self):
        run = [clip(NOW - 5000), clip(NOW - 4940), clip(NOW - 4880),
               clip(NOW - 60)]
        self.assertEqual(clips.longest_visit(run, const.LOITER_GAP), 135)

    def test_nothing_lasted_zero_seconds(self):
        """Zero rather than None: this is read into a template beside a count."""
        self.assertEqual(clips.longest_visit([], const.LOITER_GAP), 0)

    def test_visits_and_recordings_are_different_numbers(self):
        """The entire reason the sensor exists."""
        run = [clip(NOW - 240 + step * 15) for step in range(16)]
        self.assertEqual(len(run), 16)
        self.assertEqual(len(clips.sessions(run, const.LOITER_GAP)), 1)


class Entity(unittest.TestCase):
    def test_it_counts_visits_rather_than_recordings(self):
        body = SOURCE.split("class H500Visits", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("len(sessions(", body)

    def test_it_groups_with_the_same_gap_as_everything_else(self):
        """Two ideas of what one visit is would let the loitering sensor and
        this sensor disagree about how many people were at the door.

        Scoped to native_value. Checking the whole class matched the attribute
        that merely reports the gap, so a count grouped by some other number
        passed.
        """
        body = SOURCE.split("class H500Visits", 1)[1].split("\nclass ", 1)[0]
        counting = body.split("def native_value", 1)[1].split("@property", 1)[0]
        self.assertIn("LOITER_GAP", counting)

    def test_it_carries_the_shape_of_the_day(self):
        body = SOURCE.split("class H500Visits", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('"hourly": hourly_counts(', body)
        self.assertIn('"longest_seconds": longest_visit(', body)

    def test_it_has_a_label(self):
        """Without one the entity shows as `visits_24h`."""
        self.assertIn("visits_24h", STRINGS["entity"]["sensor"])

    def test_one_per_camera(self):
        self.assertIn("H500Visits(coordinator, index, camera)", SOURCE)

    def test_its_unique_id_is_the_camera_not_the_entry(self):
        """Per camera, like every other camera sensor; keyed on the entry it
        would collide the moment a second camera was added."""
        body = SOURCE.split("class H500Visits", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("camera['device_id']", body)


if __name__ == "__main__":
    unittest.main()
