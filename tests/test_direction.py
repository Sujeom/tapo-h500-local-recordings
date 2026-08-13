"""Reading a trail as a direction.

Pure, so exercised for real. Almost every test here is about NOT answering:
"someone is approaching the door" is the kind of thing people wire a siren to,
so it has to be silent whenever the answer is not actually known rather than
plausible.
"""
import importlib
import sys
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
package = types.ModuleType("tapo_h500")
package.__path__ = [str(COMPONENT)]
sys.modules.setdefault("tapo_h500", package)
clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

RANKS = {"Gate": 0, "Front Doorbell": 1}
WINDOW = const.DIRECTION_WINDOW


def trail(*hops):
    """Newest first, as faces_seen builds it."""
    return [{"camera": camera, "at": at} for camera, at in hops]


def direction(*hops, ranks=RANKS, window=WINDOW):
    return clips.direction(trail(*hops), ranks, window)


class Reading(unittest.TestCase):
    def test_moving_towards_the_door_is_approaching(self):
        self.assertEqual(
            direction(("Front Doorbell", 1000), ("Gate", 990)), "approaching")

    def test_moving_away_from_the_door_is_leaving(self):
        self.assertEqual(
            direction(("Gate", 1000), ("Front Doorbell", 990)), "leaving")

    def test_only_the_two_newest_hops_decide_it(self):
        """A visit an hour ago must not colour where someone is going now."""
        self.assertEqual(
            direction(("Front Doorbell", 1000), ("Gate", 990),
                      ("Front Doorbell", 100)), "approaching")


class Silence(unittest.TestCase):
    def test_one_sighting_is_not_a_direction(self):
        self.assertIsNone(direction(("Gate", 1000)))

    def test_nothing_at_all_is_not_a_direction(self):
        self.assertIsNone(clips.direction([], RANKS, WINDOW))

    def test_unranked_cameras_produce_no_answer(self):
        """The default state: nobody has set a layout yet."""
        self.assertIsNone(
            direction(("Front Doorbell", 1000), ("Gate", 990), ranks={}))

    def test_a_partially_ranked_layout_does_not_guess(self):
        self.assertIsNone(
            direction(("Front Doorbell", 1000), ("Gate", 990),
                      ranks={"Gate": 0}))

    def test_two_sightings_too_far_apart_are_two_visits(self):
        """Not one journey. Calling that approaching would be an invention."""
        self.assertIsNone(
            direction(("Front Doorbell", 10_000), ("Gate", 10_000 - WINDOW - 1)))

    def test_just_inside_the_window_still_counts(self):
        self.assertEqual(
            direction(("Front Doorbell", 10_000), ("Gate", 10_000 - WINDOW)),
            "approaching")

    def test_cameras_at_the_same_distance_say_nothing(self):
        """Two cameras either side of a drive are not a direction, and this is
        how someone turns the feature off."""
        self.assertIsNone(
            direction(("A", 1000), ("B", 990), ranks={"A": 1, "B": 1}))

    def test_a_hop_with_no_time_is_not_trusted(self):
        self.assertIsNone(
            clips.direction([{"camera": "Front Doorbell"}, {"camera": "Gate"}],
                            RANKS, WINDOW))


class Wiring(unittest.TestCase):
    def test_the_window_is_a_few_minutes_not_a_day(self):
        self.assertLessEqual(const.DIRECTION_WINDOW, 600)
        self.assertGreaterEqual(const.DIRECTION_WINDOW, 30)

    def test_ranks_are_keyed_by_name_not_index(self):
        """A paired-list index shifts when a camera is removed; a name does
        not, and a trail records names."""
        source = (COMPONENT / "coordinator.py").read_text()
        body = source.split("def camera_ranks", 1)[1].split("def faces_seen", 1)[0]
        self.assertIn("str(key)", body)

    def test_a_bad_rank_is_ignored_rather_than_fatal(self):
        source = (COMPONENT / "coordinator.py").read_text()
        body = source.split("def camera_ranks", 1)[1].split("def faces_seen", 1)[0]
        self.assertIn("except (TypeError, ValueError):", body)


if __name__ == "__main__":
    unittest.main()
