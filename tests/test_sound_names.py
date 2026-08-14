"""The custom sound names, which were parsed and then never shown.

`used_audio_slots` has read the hub's five slots since the sensor was added,
`hub_readings` has carried the names on every poll, and the reference
documented them as an attribute of `sensor.<hub>_custom_sounds`. That attribute
did not exist: HubSensor had no attributes hook at all, so the documentation
described a field nothing produced.

"3" is a poor answer to "which sounds does the hub hold". The names are the
content of the reading; the count is a summary of them.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "sensor.py").read_text()
REFERENCE = (Path(__file__).parents[1] / "docs" / "reference.md").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

status = importlib.import_module("tapo_h500.status")


def slots(*names):
    """A getUsrDefAudioList reply holding these named slots.

    All five always come back; the empty ones carry an empty string rather
    than being absent, which is why presence proves nothing and the name is
    what has to be checked.
    """
    files = {}
    for index in range(5):
        name = names[index] if index < len(names) else ""
        files[f"file_{index + 1}"] = {"name": name}
    return {"getUsrDefAudioList": {"usr_def_audio": files}}


class Reading(unittest.TestCase):
    def test_named_slots_are_listed(self):
        readings = status.hub_readings(slots("Dog barking", "Gate chime"))
        self.assertEqual(readings["custom_sound_names"],
                         ["Dog barking", "Gate chime"])

    def test_the_count_agrees_with_the_names(self):
        """Two answers to one question is worse than either."""
        readings = status.hub_readings(slots("Dog barking", "Gate chime"))
        self.assertEqual(readings["custom_sounds"],
                         len(readings["custom_sound_names"]))

    def test_empty_slots_are_not_sounds(self):
        readings = status.hub_readings(slots())
        self.assertEqual(readings["custom_sound_names"], [])
        self.assertEqual(readings["custom_sounds"], 0)

    def test_a_shape_nobody_anticipated_does_not_take_the_poll_down(self):
        """This runs inside the poll, beside every other reading."""
        self.assertEqual(
            status.hub_readings({"getUsrDefAudioList": {"usr_def_audio": []}})
            ["custom_sound_names"], [])


class Attached(unittest.TestCase):
    def test_the_sensor_publishes_them(self):
        # Split on the entry's own closing line: the value= line ends in "),
        # too, so a shorter terminator cuts the block off before the
        # attributes reach it.
        block = SOURCE.split('key="custom_sounds"', 1)[1].split("\n    ),", 1)[0]
        self.assertIn('attributes=lambda r: {"names":', block)

    def test_the_attribute_hook_exists_at_all(self):
        """It did not. The documented attribute was produced by nothing."""
        self.assertIn("attributes: Callable[[dict], dict] | None = None",
                      SOURCE)
        body = SOURCE.split("class H500HubSensor", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("def extra_state_attributes", body)
        self.assertIn("self.entity_description.attributes(", body)

    def test_a_reading_with_nothing_to_add_publishes_nothing(self):
        """None, not {}. An empty dictionary is a set of attributes, and Home
        Assistant would record one on every state change of every hub
        sensor."""
        body = SOURCE.split("class H500HubSensor", 1)[1].split("\nclass ", 1)[0]
        hook = body.split("def extra_state_attributes", 1)[1]
        self.assertIn("return None", hook)

    def test_only_this_reading_uses_it(self):
        """One lambda, not a hook every description now has to think about."""
        self.assertEqual(SOURCE.count("attributes=lambda"), 1)

    def test_the_reference_now_describes_something_real(self):
        self.assertIn("names as an attribute", REFERENCE)


if __name__ == "__main__":
    unittest.main()
