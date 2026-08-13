"""Detection binary sensors: one per named code, held open then cleared.

Static, since binary_sensor.py imports the Home Assistant entity platform.
What is worth protecting is the shape: a code with no label renders as a raw
key, a sensor that never clears sticks on forever because the hub only reports
that something happened and never that it stopped, and a timer left running
against a removed entity raises.
"""
import json
import re
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "binary_sensor.py").read_text()
CONST = (COMPONENT / "const.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

NAMES = dict(re.findall(r"^\s+(\d+): \"([^\"]+)\",", CONST, re.M))
SLUGS = {name.replace(" ", "_") for name in NAMES.values()}


class Coverage(unittest.TestCase):
    def test_one_sensor_per_named_detection(self):
        self.assertIn("for code in DETECTION_NAMES", SOURCE)

    def test_every_sensor_has_a_label(self):
        """Without one the entity shows as `detected_unknown_face`."""
        labels = STRINGS["entity"]["binary_sensor"]
        missing = {f"detected_{slug}" for slug in SLUGS} - set(labels)
        self.assertEqual(missing, set())

    def test_no_label_is_left_over(self):
        labels = {k for k in STRINGS["entity"]["binary_sensor"]
                  if k.startswith("detected_")}
        self.assertEqual(labels - {f"detected_{s}" for s in SLUGS}, set())


class Holding(unittest.TestCase):
    def test_it_clears_itself(self):
        """The hub never reports that something stopped, so a sensor that only
        ever turns on would stay on for the lifetime of the integration.

        Scoped to the handler: searching the whole file matches the import of
        async_call_later and passes even when nothing schedules it.
        """
        body = SOURCE.split("def _handle", 1)[1].split("def _clear", 1)[0]
        self.assertIn("async_call_later", body)
        self.assertIn("self._attr_is_on = False", SOURCE)

    def test_the_hold_outlasts_a_clip(self):
        """Shorter than the recording and one visit reads as a stutter."""
        hold = int(re.search(r"^DETECTION_HOLD = (\d+)", CONST, re.M).group(1))
        self.assertGreater(hold, 15)

    def test_a_second_detection_restarts_the_hold(self):
        """Otherwise the first detection's timer ends a presence that is still
        happening."""
        body = SOURCE.split("def _handle", 1)[1].split("def _clear", 1)[0]
        self.assertLess(body.index("self._cancel_timer()"),
                        body.index("self._attr_is_on = True"))

    def test_the_timer_is_cancelled_on_removal(self):
        """A pending callback against a removed entity raises."""
        self.assertIn("self.async_on_remove(self._cancel_timer)", SOURCE)

    def test_it_is_driven_by_the_event_signal_not_the_poll(self):
        """Same instant as the notification, rather than up to a poll later."""
        self.assertIn('self.coordinator.signal("event", self.index)', SOURCE)

    def test_it_matches_every_code_that_fired(self):
        body = SOURCE.split("def _handle", 1)[1].split("def _clear", 1)[0]
        self.assertIn("detection_types(entry)", body)
        self.assertNotIn("alarm_type", body)


if __name__ == "__main__":
    unittest.main()
