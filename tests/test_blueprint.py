"""The notification blueprint is well formed and does what it claims.

A blueprint fails in front of a user in specific ways: an input referenced but
never declared makes it unimportable, a detection code that does not match
DETECTION_NAMES silently never fires, and attaching the picture to the first
notification is the bug that showed people the previous event's photograph.
"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
PATH = ROOT / "blueprints" / "automation" / "tapo_h500" / "notify_on_detection.yaml"
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
        used = set(re.findall(r"!input (\w+)", RAW))
        self.assertEqual(used - set(INPUTS), set())

    def test_every_declared_input_is_used(self):
        """A declared input that nothing reads is a control that does nothing."""
        used = set(re.findall(r"!input (\w+)", RAW))
        self.assertEqual(set(INPUTS) - used, set())

    def test_it_declares_where_it_came_from(self):
        """Without source_url, Home Assistant cannot offer to re-import it."""
        self.assertIn("source_url", DOC["blueprint"])
        self.assertEqual(DOC["blueprint"]["domain"], "automation")

    def test_it_runs_in_parallel(self):
        """Two doorbells can fire in the same second, and the photo step waits
        up to three minutes; a single-run automation would drop the second."""
        self.assertEqual(DOC["mode"], "parallel")
        self.assertGreater(DOC["max"], 1)


class Detections(unittest.TestCase):
    def test_the_offered_codes_exist_in_the_integration(self):
        """A code that is not in DETECTION_NAMES would never match."""
        known = {int(n) for n in re.findall(r"^\s+(\d+): \"", CONST, re.M)}
        offered = {int(option["value"])
                   for option in INPUTS["detections"]["selector"]["select"]["options"]}
        self.assertEqual(offered - known, set())

    def test_motion_is_not_selected_by_default(self):
        """It fires on nearly everything; defaulting to it makes the blueprint
        a nuisance on first import."""
        self.assertNotIn(2, INPUTS["detections"]["default"])

    def test_the_defaults_are_the_three_worth_interrupting_for(self):
        self.assertEqual(sorted(INPUTS["detections"]["default"]), [6, 9, 17])

    def test_it_matches_every_code_that_fired(self):
        """Not just the headline alarm_type."""
        filter_condition = DOC["conditions"][1]["value_template"]
        self.assertIn("detection_types", filter_condition)
        self.assertNotIn("alarm_type", filter_condition)


class KnownFaces(unittest.TestCase):
    """A recognised person is named rather than called "a person"."""

    def test_it_reads_the_resolved_names_not_the_ids(self):
        """An automation cannot reach the hub's name map, so the integration
        resolves it. Reading face_ids here would put a twelve-digit number in
        a sentence."""
        self.assertIn("state_attr(trigger.entity_id, 'faces')", RAW)
        self.assertNotIn("state_attr(trigger.entity_id, 'face_ids')", RAW)

    def test_a_named_person_takes_the_headline(self):
        self.assertIn("{% if 17 in codes and who %}{{ who }} rang the", RAW)
        self.assertIn("{% elif who %}{{ who }} at {{ where }}", RAW)

    def test_an_unknown_face_still_falls_back(self):
        """Most detections are of nobody in particular."""
        self.assertIn("{% elif 17 in codes %}Someone rang the", RAW)
        self.assertIn("{% elif 6 in codes %}Person at", RAW)

    def test_naming_someone_suppresses_the_generic_words(self):
        """"Alice - a person, a familiar face" says the same thing threefold."""
        self.assertIn("set skip = [6, 20] if who else []", RAW)


class Photograph(unittest.TestCase):
    def _notifications(self):
        return re.findall(r"image: \"\{\{ frame \}\}\"", RAW)

    def test_only_the_follow_up_carries_the_picture(self):
        """The first notification must not: at that moment the hub is still
        recording and the only frame on disk is the PREVIOUS event's."""
        self.assertEqual(len(self._notifications()), 1)
        first = RAW.index("# First: immediately")
        self.assertGreater(RAW.index('image: "{{ frame }}"'), first)

    def test_the_follow_up_waits_for_this_events_own_clip(self):
        self.assertIn("wait_template", RAW)
        self.assertIn("as_timestamp(states(activity), 0) | int >= moment", RAW)

    def test_both_notifications_share_a_tag(self):
        """Same tag means the second replaces the first rather than stacking."""
        self.assertEqual(len(re.findall(r'tag: "tapo-h500-\{\{ camera \}\}"', RAW)), 2)

    def test_an_empty_frame_is_not_sent(self):
        """If the download never landed, send nothing rather than a broken
        image."""
        self.assertIn("frame not in [none, '', 'None']", RAW)


if __name__ == "__main__":
    unittest.main()
