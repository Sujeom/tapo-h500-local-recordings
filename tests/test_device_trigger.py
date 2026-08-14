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
    def test_detections_come_from_the_event_entity(self):
        self.assertIn('if entry.domain == "event":', SOURCE)

    def test_worked_out_states_come_from_binary_sensors(self):
        self.assertIn('if entry.domain != "binary_sensor":', SOURCE)

    def test_the_unique_id_tails_match_real_sensors(self):
        """Matched on the unique id rather than the entity id, which the owner
        can rename to anything -- so the tails have to be the ones the sensors
        actually use, or the triggers silently never appear."""
        tails = re.findall(r'"[\w]+": "(_[\w]+)",',
                           SOURCE.split("STATE_TRIGGERS", 1)[1].split("}", 1)[0])
        for tail in tails:
            self.assertIn(f'{tail}"', BINARY, tail)

    def test_it_matches_every_code_that_fired_not_just_the_headline(self):
        """detection_types lists everything at once. Comparing against
        alarm_type would miss a person who also tripped motion."""
        body = SOURCE.split("def _detected", 1)[1].split("state_config =", 1)[0]
        self.assertIn('state.attributes.get("detection_types")', body)
        self.assertNotIn("alarm_type", body)

    def test_each_kind_reaches_its_own_attacher(self):
        """Detections are the fall-through, so a missing branch does not fail
        loudly -- a loitering trigger would quietly be treated as a detection
        code, look up TRIGGER_TYPES["loitering"] and never fire."""
        body = SOURCE.split("async def async_attach_trigger", 1)[1] \
                     .split("\ndef _entity_id", 1)[0]
        self.assertLess(body.index("if kind in EVENT_TRIGGERS:"),
                        body.index("if kind in STATE_TRIGGERS:"))
        self.assertLess(body.index("_attach_state("),
                        body.index("_attach_detection("))

    def test_a_state_trigger_fires_on_turning_on_only(self):
        """These all clear by themselves, and firing again as somebody walks
        away is how an automation gets muted."""
        body = SOURCE.split("async def _attach_state", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn('"to": "on"', body)

    def test_a_bus_event_is_filtered_to_this_hub(self):
        """Both events are fired by every hub. Unfiltered, a two-hub
        installation announces the neighbours' front door as well."""
        body = SOURCE.split("async def _attach_event", 1)[1]
        self.assertIn('"event_data": {"entry_id"', body)

    def test_the_bus_events_belong_to_the_hub_not_a_camera(self):
        """An arrival is a person rather than a camera, and a visit can now
        span two cameras."""
        body = SOURCE.split("async def async_get_triggers", 1)[1] \
                     .split("async def async_attach_trigger", 1)[0]
        self.assertIn("if _hub_entry_id(hass, device_id):", body)

    def test_a_camera_is_not_mistaken_for_the_hub(self):
        """Both are identified as (DOMAIN, <something>), so the shape cannot
        tell them apart -- being a loaded hub can."""
        body = SOURCE.split("def _hub_entry_id", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn("DATA_HUBS", body)
        self.assertIn("identifier in hubs", body)

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
