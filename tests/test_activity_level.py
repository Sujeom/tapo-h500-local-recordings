"""One word for the last hour, instead of three numbers to join up by eye.

"Is anything going on at the side gate" currently means reading a recordings
count, an unusual-activity flag and a last-activity timestamp -- three
different questions -- and reaching a conclusion. These check that the join is
monotonic, judged against the camera's own rate, and that the busy step really
is derived from the unusual one rather than given numbers of its own.
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
WINDOW = const.LOOKBACK_SECONDS
NORMAL = const.SENSITIVITY_LEVELS["normal"]


def clip(offset):
    return {"startTime": NOW - offset, "endTime": NOW - offset + 15}


def level(recent=0, earlier=0, sensitivity=NORMAL):
    """`recent` events inside the last hour, `earlier` spread over the day."""
    multiplier, floor = sensitivity
    run = [clip(60 + step) for step in range(recent)]
    run += [clip(7200 + step * 600) for step in range(earlier)]
    return clips.activity_level(run, NOW, WINDOW, multiplier, floor)


class Levels(unittest.TestCase):
    def test_nothing_at_all_is_quiet(self):
        self.assertEqual(level(), "quiet")

    def test_nothing_recent_is_quiet_however_busy_the_day_was(self):
        """Quiet is about the last hour. A camera that saw forty people this
        morning and nobody since is quiet now."""
        self.assertEqual(level(recent=0, earlier=40), "quiet")

    def test_one_event_on_a_dead_camera_is_not_busy(self):
        """A back gate's baseline is near zero, so any event at all is
        infinitely above typical. Calling that busy is what the floor exists
        to prevent, and it applies to this word too."""
        self.assertEqual(level(recent=1), "active")

    def test_enough_events_are_unusual(self):
        self.assertEqual(level(recent=12), "unusual")

    def test_busy_sits_between_active_and_unusual(self):
        """Not a fourth independent threshold: halfway to unusual. The floor
        is 4, so 2 and 3 are busy and 4 is unusual."""
        self.assertEqual(level(recent=2), "busy")
        self.assertEqual(level(recent=3), "busy")
        self.assertEqual(level(recent=4), "unusual")

    def test_the_scale_never_goes_backwards(self):
        """Two independent pairs of numbers could make a camera busy at four
        events and merely active at five. Walk the whole range and check the
        answer only ever escalates."""
        order = {name: rank
                 for rank, name in enumerate(clips.ACTIVITY_LEVELS)}
        seen = [order[level(recent=count, earlier=8)] for count in range(0, 30)]
        self.assertEqual(seen, sorted(seen))

    def test_it_is_judged_against_this_camera_not_a_fixed_number(self):
        """The same six events are unusual on a back gate and ordinary on a
        doorbell facing a pavement."""
        self.assertEqual(level(recent=6, earlier=0), "unusual")
        self.assertEqual(level(recent=6, earlier=120), "active")

    def test_sensitivity_moves_the_line(self):
        """Relaxed needs more before it says anything; sensitive needs less."""
        self.assertEqual(
            level(recent=3, sensitivity=const.SENSITIVITY_LEVELS["sensitive"]),
            "unusual")
        self.assertEqual(
            level(recent=3, sensitivity=const.SENSITIVITY_LEVELS["relaxed"]),
            "active")


class AgreesWithTheFlag(unittest.TestCase):
    """The unusual-activity binary sensor and this word are the same
    measurement. Two answers to one question is worse than either."""

    def test_unusual_means_exactly_what_the_flag_means(self):
        multiplier, floor = NORMAL
        for recent in range(0, 20):
            run = [clip(60 + step) for step in range(recent)]
            flagged = clips.unusually_busy(run, NOW, WINDOW, multiplier, floor)
            word = clips.activity_level(run, NOW, WINDOW, multiplier, floor)
            self.assertEqual(flagged, word == "unusual", f"at {recent} events")

    def test_the_threshold_is_shared_rather_than_restated(self):
        source = (COMPONENT / "clips.py").read_text()
        body = source.split("def unusually_busy", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("unusual_threshold(", body)


class Entity(unittest.TestCase):
    def test_it_declares_every_level_it_can_report(self):
        """An enum sensor reporting a state outside its options logs an error
        and shows unknown."""
        body = SOURCE.split("class H500ActivityLevel", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_attr_options = list(ACTIVITY_LEVELS)", body)

    def test_every_level_has_a_label(self):
        states = STRINGS["entity"]["sensor"]["activity_level"]["state"]
        self.assertEqual(set(states), set(clips.ACTIVITY_LEVELS))

    def test_it_uses_the_per_camera_sensitivity(self):
        """Scoped to native_value: the attributes read it too, so checking the
        whole class passed while the state itself used fixed numbers -- and
        the attributes would then have explained a decision they did not
        make."""
        body = SOURCE.split("class H500ActivityLevel", 1)[1].split("\ndef ", 1)[0]
        state = body.split("def native_value", 1)[1].split("@property", 1)[0]
        self.assertIn("self.coordinator.sensitivity(self.index)", state)

    def test_one_per_camera(self):
        self.assertIn("H500ActivityLevel(coordinator, index, camera)", SOURCE)

    def test_its_unique_id_is_the_camera(self):
        body = SOURCE.split("class H500ActivityLevel", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("camera['device_id']", body)

    def test_it_says_what_it_was_measured_against(self):
        body = SOURCE.split("class H500ActivityLevel", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"unusual_at"', body)
        self.assertIn('"busy_at"', body)


if __name__ == "__main__":
    unittest.main()
