"""Standing at the door is not the same as walking past it.

The hub reports moments, never presence: four minutes at the door arrives as a
string of short clips, and every other signal in this integration counts those
as separate events. These tests are about grouping them back into a visit and
about the three things that have to hold before the word "loitering" is used --
the face is one the hub could not match, the visit is still open, and it lasted
long enough to mean something.
"""
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
BINARY_SENSOR = (COMPONENT / "binary_sensor.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
UNKNOWN_FACE = 1 << (22 - 1)
KNOWN_FACE = 1 << (20 - 1)
MOTION = 1 << (2 - 1)


def clip(ago, length=15, mask=UNKNOWN_FACE):
    return {"startTime": NOW - ago, "endTime": NOW - ago + length,
            "events_1": mask}


def loiter(items, now=NOW):
    return clips.loitering(items, now, const.LOITER_GAP, const.LOITER_SECONDS)


class Sessions(unittest.TestCase):
    def test_recordings_close_together_are_one_visit(self):
        visits = clips.sessions([clip(300), clip(270), clip(240)], 120)
        self.assertEqual(len(visits), 1)
        self.assertEqual(visits[0][2], 3)

    def test_a_long_silence_starts_a_new_visit(self):
        visits = clips.sessions([clip(3600), clip(300)], 120)
        self.assertEqual(len(visits), 2)

    def test_a_visit_spans_first_to_last(self):
        visits = clips.sessions([clip(300, length=15), clip(200, length=15)], 120)
        start, end, _ = visits[0]
        self.assertEqual(start, NOW - 300)
        self.assertEqual(end, NOW - 200 + 15)

    def test_visits_come_back_oldest_first(self):
        visits = clips.sessions([clip(60), clip(3600), clip(7200)], 120)
        self.assertEqual([start for start, _, _ in visits],
                         sorted(start for start, _, _ in visits))

    def test_recordings_out_of_order_are_sorted_first(self):
        """The hub is not promised to return these oldest-first, and walking
        an unsorted list merges everything: the step back in time is a
        negative gap, which is smaller than any threshold, so two visits hours
        apart collapse into one."""
        visits = clips.sessions([clip(30), clip(3600), clip(0)], 120)
        self.assertEqual(len(visits), 2)

    def test_a_recording_with_no_start_is_dropped(self):
        self.assertEqual(clips.sessions([{"endTime": NOW}], 120), [])

    def test_a_recording_with_no_end_is_a_moment(self):
        visits = clips.sessions([{"startTime": NOW - 60}], 120)
        self.assertEqual(visits, [(NOW - 60, NOW - 60, 1)])


class Loitering(unittest.TestCase):
    def test_a_long_visit_by_an_unknown_face_counts(self):
        someone = [clip(ago) for ago in range(300, 0, -30)]
        self.assertGreaterEqual(loiter(someone), const.LOITER_SECONDS)

    def test_a_brief_visit_does_not(self):
        """A delivery is under a minute at the door. Flagging that is how a
        signal becomes noise and gets muted."""
        self.assertEqual(loiter([clip(40), clip(20)]), 0)

    def test_a_recognised_face_never_counts(self):
        """Somebody the hub knows, waiting at their own door, is not a
        concern -- and this is the signal a siren gets wired to."""
        household = [clip(ago, mask=KNOWN_FACE) for ago in range(300, 0, -30)]
        self.assertEqual(loiter(household), 0)

    def test_motion_alone_never_counts(self):
        weather = [clip(ago, mask=MOTION) for ago in range(300, 0, -30)]
        self.assertEqual(loiter(weather), 0)

    def test_a_visit_that_ended_is_over(self):
        """Somebody who stood there for five minutes two hours ago is not
        loitering now."""
        earlier = [clip(7200 + ago) for ago in range(300, 0, -30)]
        self.assertEqual(loiter(earlier), 0)

    def test_it_measures_sightings_not_silence(self):
        """One fifteen-second clip is evidence of fifteen seconds. Measuring
        to now instead would inflate every brief visit the moment it ended,
        and every camera would loiter."""
        self.assertEqual(loiter([clip(100, length=15)]), 0)

    def test_the_quiet_since_the_last_sighting_is_not_part_of_the_visit(self):
        """Sightings covering 100 seconds, ending 100 seconds ago. Counting
        from the first sighting to now would make that 200 and cross the
        threshold on silence alone."""
        brief = [clip(200, length=15), clip(115, length=15)]
        self.assertEqual(loiter(brief), 0)

    def test_an_earlier_visit_does_not_answer_for_the_current_one(self):
        """Two visits in the window: a brief one hours ago and a long one
        happening now. Reading the wrong end of the list reports on the wrong
        person."""
        earlier = [clip(7200), clip(7180)]
        current = [clip(ago) for ago in range(300, 0, -30)]
        self.assertGreaterEqual(loiter(earlier + current),
                                const.LOITER_SECONDS)

    def test_two_short_visits_do_not_add_up(self):
        """Twice past the door for a minute each is not four minutes there."""
        twice = [clip(3600), clip(3630), clip(60), clip(30)]
        self.assertEqual(loiter(twice), 0)

    def test_nothing_at_all_is_zero(self):
        self.assertEqual(loiter([]), 0)


class Entity(unittest.TestCase):
    def test_the_camera_gets_a_loitering_sensor(self):
        declaration = BINARY_SENSOR.split("async_setup_entry", 1)[1]
        declaration = declaration.split("class ", 1)[0]
        self.assertIn("H500Loitering(coordinator, index, camera)", declaration)

    def test_it_is_a_problem_rather_than_occupancy(self):
        body = BINARY_SENSOR.split("class H500Loitering", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn("BinarySensorDeviceClass.PROBLEM", body)

    def test_it_reports_the_duration_as_an_attribute(self):
        body = BINARY_SENSOR.split("class H500Loitering", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn('"seconds"', body)


if __name__ == "__main__":
    unittest.main()
