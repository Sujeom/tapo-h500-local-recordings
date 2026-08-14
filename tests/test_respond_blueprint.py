"""The respond blueprint acts rather than telling you about it.

Every piece was already here -- a siren entity, a night signal, resolved names
-- and nothing wired them together. What matters about a blueprint that turns
on a siren is what it does NOT do: it must be quiet by default, it must
respect the snooze, and it must not fire on a cat at three in the morning.
"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
PATH = ROOT / "blueprints" / "automation" / "tapo_h500" / "respond_to_activity.yaml"
RAW = PATH.read_text()
CONST = (ROOT / "custom_components" / "tapo_h500" / "const.py").read_text()


class _Loader(yaml.SafeLoader):
    """!input is Home Assistant's own tag; keep it as data so this parses."""


_Loader.add_constructor(
    "!input", lambda loader, node: {"__input__": loader.construct_scalar(node)})
DOC = yaml.load(RAW, Loader=_Loader)
INPUTS = DOC["blueprint"]["input"]


class Structure(unittest.TestCase):
    def test_every_referenced_input_is_declared(self):
        """An undeclared !input makes the blueprint refuse to import."""
        self.assertEqual(set(re.findall(r"!input (\w+)", RAW)) - set(INPUTS),
                         set())

    def test_every_declared_input_is_used(self):
        """A declared input nothing reads is a control that does nothing."""
        self.assertEqual(set(INPUTS) - set(re.findall(r"!input (\w+)", RAW)),
                         set())

    def test_it_declares_where_it_came_from(self):
        self.assertIn("source_url", DOC["blueprint"])
        self.assertEqual(DOC["blueprint"]["domain"], "automation")

    def test_it_does_not_run_twice_at_once(self):
        """Two people arriving together produce two events a second apart.
        Running the whole sequence twice means the second run turns the lights
        off while the first is still waiting."""
        self.assertEqual(DOC["mode"], "single")


class QuietByDefault(unittest.TestCase):
    def test_the_siren_is_not_chosen_for_you(self):
        """A siren that goes off at three in the morning because a cat walked
        past is a siren that gets unplugged."""
        self.assertEqual(INPUTS["siren_entity"]["default"], "")

    def test_nothing_sounds_without_one(self):
        self.assertIn("input_siren_entity != ''", RAW)

    def test_no_lights_are_chosen_for_you(self):
        self.assertEqual(INPUTS["lights"]["default"], [])

    def test_announcing_needs_both_an_engine_and_a_speaker(self):
        """tts.speak names them separately and fails the whole run if either
        is missing, which would take the lights and siren down with it."""
        self.assertEqual(INPUTS["tts_entity"]["default"], "")
        self.assertEqual(INPUTS["speaker"]["default"], "")
        self.assertIn("input_tts_entity != '' and input_speaker != ''", RAW)

    def test_it_defaults_to_the_one_detection_that_is_neither_a_cat_nor_a_delivery(self):
        self.assertEqual(INPUTS["detections"]["default"], ["22"])

    def test_and_to_night_only(self):
        """An unknown face at three in the afternoon is a delivery."""
        self.assertIs(INPUTS["night_only"]["default"], True)


class Gates(unittest.TestCase):
    def _conditions(self):
        """The condition templates themselves, and nothing around them.

        Not the raw text: a comment above a condition matched "detection_types"
        while the code beside it had been changed to alarm_type, so the test
        passed over a real break. Comments explaining a check are not the
        check. Not yaml.dump either -- that re-wraps every template, so a
        pattern spanning a line break stops matching for reasons that have
        nothing to do with the blueprint.
        """
        return "\n".join(str(condition.get("value_template", ""))
                         for condition in DOC["conditions"])

    def test_the_night_gate_uses_the_integration_signal(self):
        """A window that wraps midnight is the obvious thing to get wrong,
        and it is already decided once, in the integration."""
        self.assertIn("notable", self._conditions())
        self.assertNotIn("night_start", RAW)

    def test_the_night_gate_can_be_turned_off(self):
        self.assertIn("not input_night_only", self._conditions())

    def test_the_snooze_is_respected(self):
        self.assertIn("input_snooze_entity", self._conditions())

    def test_an_empty_gate_never_blocks(self):
        """Every optional gate defaults to empty, and an empty one must mean
        "no condition" rather than "never"."""
        conditions = self._conditions()
        for name in ("input_snooze_entity", "input_armed_entity"):
            self.assertRegex(conditions, rf"not {name}\s*\n?\s*or ")

    def test_restored_states_are_skipped(self):
        """An event entity restores its last event on restart. Without this
        the siren sounds every time Home Assistant comes back up."""
        self.assertIn("'unknown', 'unavailable'", self._conditions())

    def test_it_matches_on_everything_that_fired(self):
        """detection_types lists them all; alarm_type reports only the most
        significant, so matching on that misses an unfamiliar face that also
        tripped motion."""
        self.assertIn("detection_types", self._conditions())


class Codes(unittest.TestCase):
    def test_every_offered_code_is_one_the_hub_names(self):
        """An option that does not match DETECTION_NAMES silently never
        fires."""
        known = set(re.findall(r"^    (\d+): ", CONST, re.M))
        offered = {option["value"]
                   for option in INPUTS["detections"]["selector"]["select"]
                   ["options"]}
        self.assertEqual(offered - known, set())

    def test_plain_motion_is_not_offered(self):
        """It fires on nearly everything the camera sees. Wiring a siren to
        it is how the whole thing gets turned off."""
        offered = {option["value"]
                   for option in INPUTS["detections"]["selector"]["select"]
                   ["options"]}
        self.assertNotIn("2", offered)


class Announcement(unittest.TestCase):
    def test_an_unnamed_face_is_not_read_out_as_a_number(self):
        """Reading "face 481036337152" to a room is worse than saying
        nothing."""
        spoken = RAW.split("      spoken: >-", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("face_ids", spoken)
        self.assertIn("Somebody unrecognised", spoken)

    def test_it_uses_the_names_the_integration_resolved(self):
        """An automation cannot read the hub's name map itself."""
        self.assertIn("state_attr(trigger.entity_id, 'faces')", RAW)

    def test_it_is_a_sentence_rather_than_a_headline(self):
        spoken = RAW.split("      spoken: >-", 1)[1].split("\n\n", 1)[0]
        self.assertIn("is at the", spoken)


class Lights(unittest.TestCase):
    def test_they_are_turned_off_again(self):
        self.assertIn("homeassistant.turn_off", RAW)

    def test_zero_minutes_leaves_them_on(self):
        self.assertIn("input_light_minutes | int > 0", RAW)

    def test_nothing_is_turned_off_that_was_not_turned_on(self):
        """The off branch must be gated on lights having been chosen too, or
        it delays for five minutes and then targets an empty list."""
        self.assertIn("input_lights | count > 0 and input_light_minutes",
                      RAW)


class Templating(unittest.TestCase):
    """YAML is parsed before any template runs, so Jinja can fill a value in
    but cannot add or remove a mapping key or a list entry. Writing one that
    tried made the notification blueprint unparseable, which is why every
    optional step here is an `if` block rather than a templated action list.
    """

    def test_every_optional_step_survives_parsing_as_a_real_action(self):
        steps = [action for action in DOC["actions"] if "if" in action]
        # Lights on, siren, announce, lights off.
        self.assertEqual(len(steps), 4)
        for step in steps:
            self.assertIsInstance(step["then"], list)
            self.assertTrue(step["then"])

    def test_no_action_is_a_bare_template_string(self):
        """An action built as one template value is a step Home Assistant
        cannot show, validate or trace."""
        for action in DOC["actions"]:
            self.assertIsInstance(action, dict)


if __name__ == "__main__":
    unittest.main()
