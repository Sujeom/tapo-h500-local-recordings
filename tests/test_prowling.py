"""Going round the house, rather than up to the door.

A visitor passes each camera once. Somebody circling comes back to one they
have already been past, and that return is the whole signal -- which is why
this needs no camera layout, unlike direction. The cases that matter are the
ones that look similar and are not: waiting at one door for a while, and two
separate visits hours apart.
"""
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
BINARY_SENSOR = (COMPONENT / "binary_sensor.py").read_text()
COORDINATOR = (COMPONENT / "coordinator.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000


def trail(*hops):
    """Newest first, the way faces_seen builds it. Each hop is (camera,
    seconds ago)."""
    return [{"camera": camera, "at": NOW - ago} for camera, ago in hops]


def prowl(*hops, window=const.PROWL_WINDOW):
    return clips.prowling(trail(*hops), window)


class Prowling(unittest.TestCase):
    def test_coming_back_to_a_camera_is_a_circuit(self):
        self.assertTrue(prowl(("Front", 30), ("Side", 90), ("Front", 150)))

    def test_two_cameras_are_enough(self):
        """The hardware this was written for has two doorbells. Requiring
        three distinct places would make the whole thing unreachable."""
        self.assertTrue(prowl(("Front", 30), ("Side", 60), ("Front", 120)))

    def test_walking_up_to_the_door_is_not(self):
        self.assertFalse(prowl(("Front", 30), ("Side", 90)))

    def test_one_sighting_is_not(self):
        self.assertFalse(prowl(("Front", 30)))

    def test_nothing_at_all_is_not(self):
        self.assertFalse(clips.prowling([], const.PROWL_WINDOW))

    def test_waiting_at_one_door_is_not(self):
        """Three clips at the front door is somebody standing there, which the
        loitering sensor is for. Consecutive sightings at one camera collapse
        before anything is called a return."""
        self.assertFalse(prowl(("Front", 30), ("Front", 60), ("Front", 90)))

    def test_and_neither_is_waiting_then_leaving(self):
        self.assertFalse(prowl(("Side", 30), ("Front", 90), ("Front", 120)))

    def test_two_visits_hours_apart_are_not_one_circuit(self):
        """Coming to the front door this morning and again this evening is
        two visits. Without the window every regular visitor would prowl."""
        self.assertFalse(prowl(("Front", 30), ("Side", 90), ("Front", 7200)))

    def test_a_hop_with_no_time_is_ignored(self):
        """It cannot be placed inside the window, so it cannot be part of a
        lap. Dropping it here leaves Front, Front: one place, no return."""
        broken = [{"camera": "Front", "at": NOW - 30},
                  {"camera": "Side"},
                  {"camera": "Front", "at": NOW - 150}]
        self.assertFalse(clips.prowling(broken, const.PROWL_WINDOW))

    def test_a_timeless_hop_does_not_break_a_real_circuit(self):
        broken = [{"camera": "Front", "at": NOW - 30},
                  {"camera": "Side", "at": NOW - 90},
                  {"camera": "Back"},
                  {"camera": "Front", "at": NOW - 200}]
        self.assertTrue(clips.prowling(broken, const.PROWL_WINDOW))

    def test_a_hop_with_no_camera_is_ignored(self):
        broken = [{"camera": "Front", "at": NOW - 30},
                  {"at": NOW - 60},
                  {"camera": "Front", "at": NOW - 90}]
        # Front, Front once the nameless hop is dropped: one place, no return.
        self.assertFalse(clips.prowling(broken, const.PROWL_WINDOW))

    def test_a_longer_lap_still_counts(self):
        self.assertTrue(prowl(("Front", 30), ("Back", 120), ("Side", 240),
                              ("Front", 400)))


class Face(unittest.TestCase):
    def test_the_flag_is_computed_per_face(self):
        self.assertIn('face["prowling"] = prowling(', COORDINATOR)

    def test_it_uses_the_prowl_window_not_the_direction_one(self):
        """The direction window covers one hop between adjacent cameras. A
        lap of the house is a walk, not a step."""
        line = [row for row in COORDINATOR.splitlines()
                if 'face["prowling"] = prowling(' in row][0]
        self.assertIn("PROWL_WINDOW", line)


class Entity(unittest.TestCase):
    def test_the_hub_gets_a_prowling_sensor(self):
        setup = BINARY_SENSOR.split("async_setup_entry", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn("H500Prowling(coordinator, entry)", setup)

    def test_it_names_who(self):
        body = BINARY_SENSOR.split("class H500Prowling", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn('"faces"', body)
        self.assertIn('"name"', body)

    def test_it_needs_no_camera_layout(self):
        """Unlike direction, which is silent until the layout is filled in.
        A circuit is about returning, not about which way is the street."""
        body = BINARY_SENSOR.split("class H500Prowling", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertNotIn("camera_ranks", body)
        function = clips.prowling.__code__
        self.assertNotIn("ranks", function.co_varnames)


if __name__ == "__main__":
    unittest.main()
