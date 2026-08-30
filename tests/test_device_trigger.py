"""Device triggers cover everything worth automating on, and all are translated.

Checked statically: device_trigger.py imports the Home Assistant device
automation helpers, which are not installed here. What can be checked is the
part that breaks visibly -- a trigger with no phrase in en.json renders in the
automation editor as a bare slug like `unknown_face`, and anything added
without a phrase would ship exactly that.

Three kinds now. Detections off the camera's event entity, worked-out states
off their binary sensors, and the two bus events that fire once per person
rather than once per recording.
"""
import json
import re
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "device_trigger.py").read_text()
CONST = (COMPONENT / "const.py").read_text()
BINARY = (COMPONENT / "binary_sensor.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())
PHRASES = STRINGS["device_automation"]["trigger_type"]

# The names as const.py declares them, so the two cannot drift.
NAMES = dict(re.findall(r"^\s+(\d+): \"([^\"]+)\",", CONST, re.M))
SLUGS = {name.replace(" ", "_") for name in NAMES.values()}

STATE_SLUGS = set(re.findall(
    r'^    "([\w]+)": "_[\w]+",',
    SOURCE.split("STATE_TRIGGERS", 1)[1].split("}", 1)[0], re.M))
EVENT_SLUGS = set(re.findall(
    r'^    "([\w]+)": EVENT_[\w]+,',
    SOURCE.split("EVENT_TRIGGERS", 1)[1].split("}", 1)[0], re.M))

# Everything that belongs to one camera. The rest is about the house.
PER_CAMERA = SLUGS | {"loitering", "possible_delivery"}


class Coverage(unittest.TestCase):
    def test_every_named_detection_becomes_a_trigger(self):
        """The point of the feature: nine codes identified, nine triggers."""
        self.assertEqual(len(SLUGS), 9, "DETECTION_NAMES changed shape")
        self.assertRegex(
            SOURCE, r"TRIGGER_TYPES: dict\[str, int\] = \{\s*\n\s*name\.replace")

    def test_the_worked_out_signals_are_offered_too(self):
        """Loitering, a possible delivery and a circuit of the house are the
        three things here that a camera cannot report and this works out."""
        self.assertEqual(
            STATE_SLUGS, {"loitering", "possible_delivery", "prowling"})

    def test_the_once_per_person_events_are_offered_too(self):
        """The two that fire once per visitor rather than once per recording,
        which is the whole reason they exist -- and both carry data no entity
        state can."""
        self.assertEqual(EVENT_SLUGS, {"arrival", "visit"})

    def test_every_trigger_has_a_phrase(self):
        """An untranslated trigger reads as `unknown_face` in the UI."""
        self.assertEqual(
            (SLUGS | STATE_SLUGS | EVENT_SLUGS) - set(PHRASES), set())

    def test_no_phrase_is_left_over(self):
        """A phrase for a trigger that no longer exists is dead weight."""
        self.assertEqual(set(PHRASES) - (SLUGS | STATE_SLUGS | EVENT_SLUGS),
                         set())

    def test_per_camera_phrases_name_the_camera(self):
        """HA substitutes {entity_name}; without it every trigger reads the
        same in a list covering two doorbells."""
        for slug in PER_CAMERA:
            self.assertIn("{entity_name}", PHRASES[slug], slug)

    def test_house_wide_phrases_do_not(self):
        """The entity there is the hub, so the phrase would read "Tapo H500
        (192.168.11.5): someone went round the house", which is worse than
        the sentence on its own."""
        for slug in ("prowling", "arrival", "visit"):
            self.assertNotIn("{entity_name}", PHRASES[slug], slug)


class Wiring(unittest.TestCase):
    # Everything this class used to read out of the source is driven in
    # test_device_trigger_live: which entity each kind of trigger comes from,
    # that a sensor is matched on its unique id, that a detection matches
    # every code that fired rather than the headline one, that each slug
    # reaches its own attacher, that a state trigger fires only on turning
    # on, that a bus event is filtered to its own hub, and that a camera is
    # not mistaken for one. What is left here is the schema, which cannot be
    # run: voluptuous is stubbed, so nothing can submit a stored automation
    # to it and see what comes back.

    def test_the_entity_id_is_optional(self):
        """The bus events have no entity behind them, and a Required key would
        make the stored automation invalid."""
        schema = SOURCE.split("TRIGGER_SCHEMA = ", 1)[1].split("})", 1)[0]
        self.assertIn("vol.Optional(CONF_ENTITY_ID)", schema)

    def test_every_slug_is_accepted_by_the_schema(self):
        schema = SOURCE.split("TRIGGER_SCHEMA = ", 1)[1].split("})", 1)[0]
        self.assertIn("list(TRIGGER_TYPES) + list(STATE_TRIGGERS)", schema)
        self.assertIn("list(EVENT_TRIGGERS)", schema)

    def test_an_older_stored_entity_id_still_resolves(self):
        """Device triggers used to store the entity id, not the registry id.
        An automation written then must not silently stop firing."""
        self.assertIn("async_validate_entity_id", SOURCE)


if __name__ == "__main__":
    unittest.main()
