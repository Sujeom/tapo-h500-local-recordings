"""Device triggers cover every detection, and every one is translated.

Checked statically: device_trigger.py imports the Home Assistant device
automation helpers, which are not installed here. What can be checked is the
part that breaks visibly -- a trigger with no phrase in en.json renders in the
automation editor as a bare slug like `unknown_face`, and a code added to
DETECTION_NAMES without a phrase would ship exactly that.
"""
import json
import re
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "device_trigger.py").read_text()
CONST = (COMPONENT / "const.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

# The names as const.py declares them, so the two cannot drift.
NAMES = dict(re.findall(r"^\s+(\d+): \"([^\"]+)\",", CONST, re.M))
SLUGS = {name.replace(" ", "_") for name in NAMES.values()}


class Coverage(unittest.TestCase):
    def test_every_named_detection_becomes_a_trigger(self):
        """The point of the feature: nine codes identified, nine triggers."""
        self.assertEqual(len(SLUGS), 9, "DETECTION_NAMES changed shape")
        self.assertRegex(
            SOURCE, r"TRIGGER_TYPES: dict\[str, int\] = \{\s*\n\s*name\.replace")

    def test_every_trigger_has_a_phrase(self):
        """An untranslated trigger reads as `unknown_face` in the UI."""
        phrases = STRINGS["device_automation"]["trigger_type"]
        self.assertEqual(SLUGS - set(phrases), set())

    def test_no_phrase_is_left_over(self):
        """A phrase for a code that no longer exists is dead weight."""
        phrases = STRINGS["device_automation"]["trigger_type"]
        self.assertEqual(set(phrases) - SLUGS, set())

    def test_phrases_name_the_entity(self):
        """HA substitutes {entity_name}; without it every trigger reads the
        same in a list covering two doorbells."""
        for slug, phrase in STRINGS["device_automation"]["trigger_type"].items():
            self.assertIn("{entity_name}", phrase, slug)


class Behaviour(unittest.TestCase):
    def test_triggers_come_from_the_event_entity_only(self):
        """The hub device has no event entity and must offer no triggers."""
        self.assertIn('if entry.domain != "event":', SOURCE)

    def test_it_matches_every_code_that_fired_not_just_the_headline(self):
        """detection_types lists everything at once. Comparing against
        alarm_type would miss a person who also tripped motion."""
        # Scoped to the matching function: the module docstring names
        # alarm_type while explaining why it is not what gets compared.
        body = SOURCE.split("def _detected", 1)[1].split("state_config =", 1)[0]
        self.assertIn('state.attributes.get("detection_types")', body)
        self.assertNotIn("alarm_type", body)

    def test_an_older_stored_entity_id_still_resolves(self):
        """Device triggers used to store the entity id, not the registry id.
        An automation written then must not silently stop firing."""
        self.assertIn("async_validate_entity_id", SOURCE)


if __name__ == "__main__":
    unittest.main()
