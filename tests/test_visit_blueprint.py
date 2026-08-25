"""The announce blueprint fires once per visitor, not once per recording.

The other two blueprints trigger on detections, which is right for a doorbell
press and wrong for a person: four minutes at the door is sixteen clips. This
one triggers on the visit event instead, and what matters about it is the same
thing that matters about the event -- that it does not turn back into sixteen
messages, and that it stays quiet when it has been told to.
"""
import asyncio
from datetime import datetime
import importlib
import re
import sys
import types
import unittest
from pathlib import Path

import jinja2
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
component_const = importlib.import_module("tapo_h500.const")

ROOT = Path(__file__).parents[1]
PATH = ROOT / "blueprints" / "automation" / "tapo_h500" / "announce_a_visit.yaml"
RAW = PATH.read_text()
COORDINATOR = (ROOT / "custom_components" / "tapo_h500" / "coordinator.py").read_text()
CONST = (ROOT / "custom_components" / "tapo_h500" / "const.py").read_text()


class _Loader(yaml.SafeLoader):
    """!input is Home Assistant's own tag; keep it as data so this parses."""


_Loader.add_constructor(
    "!input", lambda loader, node: {"__input__": loader.construct_scalar(node)})
DOC = yaml.load(RAW, Loader=_Loader)
INPUTS = DOC["blueprint"]["input"]
CONDITIONS = " ".join(
    condition.get("value_template", "") for condition in DOC["conditions"])

# The condition templates, run for real. Home Assistant's template engine is
# Jinja with extra filters; none of these use one, so plain Jinja renders them
# exactly as HA would -- and a condition that is only grepped for is a
# condition that can be inverted without any test noticing.
JINJA = jinja2.Environment()  # noqa: S701 - not HTML, no autoescape wanted


def _passes(index: int, data: dict, **context) -> bool:
    """Render one of the blueprint's conditions against a visit event."""
    template = DOC["conditions"][index]["value_template"]
    event = types.SimpleNamespace(
        event=types.SimpleNamespace(data=data))
    rendered = JINJA.from_string(template).render(
        trigger=event,
        states=lambda entity: context.get("state", "off"),
        input_detections=context.get("detections", []),
        input_who=context.get("who", "anyone"),
        input_night_only=context.get("night_only", False),
        input_snooze_entity=context.get("snooze", ""),
        input_announce_from=context.get("announce_from", ""),
        input_announce_until=context.get("announce_until", ""),
        # Home Assistant supplies now(); the window gate is the only condition
        # that reads the clock, so the test decides what time it is.
        now=lambda: datetime.strptime(context.get("clock", "12:00"), "%H:%M"),
    )
    return rendered.strip().lower() == "true"


DETECTIONS, WHO, NIGHT, SNOOZE, WINDOW = 0, 1, 2, 3, 4


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


class OncePerVisitor(unittest.TestCase):
    def test_it_triggers_on_the_visit_event(self):
        """Not on the detection entity. That is the entire difference between
        this and the notify blueprint, and the reason this one exists."""
        triggers = DOC["triggers"]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["trigger"], "event")
        self.assertEqual(triggers[0]["event_type"], "tapo_h500_visit")

    def test_that_event_name_is_the_one_the_integration_fires(self):
        """A blueprint listening for an event nobody sends is silent, and
        nothing anywhere reports that."""
        self.assertIn('EVENT_VISIT = f"{DOMAIN}_visit"', CONST)

    def test_a_second_visit_replaces_the_first_notification(self):
        """One tag per camera. Stacking them would undo the point of firing
        once per visitor."""
        self.assertIn('tag: "tapo-h500-visit-{{ where | slugify }}"', RAW)

    def test_two_visitors_at_once_both_get_a_message(self):
        """Unlike the respond blueprint, nothing here waits or turns anything
        off afterwards, so there is no sequence for a second run to trample."""
        self.assertEqual(DOC["mode"], "parallel")


VISIT = {"camera": "Front", "cameras": ["Front"], "at": 1_786_600_000,
         "detections": [2, 6], "detection": "motion + person",
         "face_ids": [], "names": [], "night": False, "recordings": 1}


class QuietWhenTold(unittest.TestCase):
    def test_the_snooze_switch_silences_it(self):
        self.assertTrue(_passes(SNOOZE, VISIT, snooze="switch.snoozed",
                                state="off"))
        self.assertFalse(_passes(SNOOZE, VISIT, snooze="switch.snoozed",
                                 state="on"))

    def test_no_snooze_switch_is_not_a_snooze(self):
        """An unset optional entity must not read as "always silent"."""
        self.assertTrue(_passes(SNOOZE, VISIT, snooze="", state="on"))

    def test_no_window_is_not_a_closed_window(self):
        """Both ends unset means announce whenever, not announce never. Half a
        window is a guess about which half was meant, so it is ignored too."""
        self.assertIs(INPUTS["announce_from"]["default"], "")
        self.assertIs(INPUTS["announce_until"]["default"], "")
        self.assertTrue(_passes(WINDOW, VISIT, clock="03:00"))
        self.assertTrue(_passes(WINDOW, VISIT, announce_from="08:00",
                                clock="03:00"))
        self.assertTrue(_passes(WINDOW, VISIT, announce_until="22:00",
                                clock="03:00"))

    def test_a_daytime_window_speaks_inside_it_and_not_outside(self):
        day = {"announce_from": "08:00", "announce_until": "22:00"}
        for clock in ("08:00", "12:00", "21:59"):
            with self.subTest(clock=clock):
                self.assertTrue(_passes(WINDOW, VISIT, clock=clock, **day))
        for clock in ("07:59", "22:00", "03:00"):
            with self.subTest(clock=clock):
                self.assertFalse(_passes(WINDOW, VISIT, clock=clock, **day))

    def test_a_window_that_wraps_midnight_is_one_night_not_an_empty_set(self):
        """The case the extra branch exists for. Comparing the clock against
        both ends the obvious way makes 22:00-07:00 match nothing at all."""
        night = {"announce_from": "22:00", "announce_until": "07:00"}
        for clock in ("22:00", "23:30", "00:01", "06:59"):
            with self.subTest(clock=clock):
                self.assertTrue(_passes(WINDOW, VISIT, clock=clock, **night))
        for clock in ("07:00", "12:00", "21:59"):
            with self.subTest(clock=clock):
                self.assertFalse(_passes(WINDOW, VISIT, clock=clock, **night))

    def test_the_window_accepts_a_seconds_suffix(self):
        """HA's time selector hands back HH:MM:SS, so the gate slices to five
        characters. A window stored with seconds must behave the same."""
        self.assertTrue(_passes(WINDOW, VISIT, announce_from="08:00:00",
                                announce_until="22:00:00", clock="12:00"))
        self.assertFalse(_passes(WINDOW, VISIT, announce_from="08:00:00",
                                 announce_until="22:00:00", clock="03:00"))

    def test_it_announces_every_visit_by_default(self):
        """An empty detection list is no filter. A blueprint that shipped with
        a filter nobody chose would look broken rather than quiet."""
        self.assertEqual(INPUTS["detections"]["default"], [])
        self.assertTrue(_passes(DETECTIONS, VISIT, detections=[]))

    def test_a_chosen_detection_filters(self):
        self.assertTrue(_passes(DETECTIONS, VISIT, detections=["6"]))
        self.assertFalse(_passes(DETECTIONS, VISIT, detections=["17"]))

    def test_one_matching_code_among_several_is_enough(self):
        """The hub reports everything that fired at once, so a press that was
        also a person matches either."""
        press = {**VISIT, "detections": [2, 6, 10, 17]}
        self.assertTrue(_passes(DETECTIONS, press, detections=["17"]))

    def test_the_night_gate_is_off_by_default(self):
        """The point of a once-per-visitor message is that it is quiet enough
        to leave on all day."""
        self.assertIs(INPUTS["night_only"]["default"], False)
        self.assertTrue(_passes(NIGHT, VISIT, night_only=False))

    def test_the_night_gate_uses_the_integration_s_own_answer(self):
        """A window wrapping midnight is the obvious thing to get wrong: 23 is
        inside 22-to-6 and 12 is not, and comparing naively marks the whole day
        as night."""
        self.assertFalse(_passes(NIGHT, VISIT, night_only=True))
        self.assertTrue(_passes(NIGHT, {**VISIT, "night": True},
                                night_only=True))
        self.assertNotIn("now().hour", RAW)

    def test_strangers_only_is_offered(self):
        """The useful setting on a busy door: the household crossing the front
        camera all day is not news."""
        values = [option["value"]
                  for option in INPUTS["who"]["selector"]["select"]["options"]]
        self.assertEqual(sorted(values), ["anyone", "named", "strangers"])
        self.assertEqual(INPUTS["who"]["default"], "anyone")

    def test_the_who_filter_actually_filters(self):
        known = {**VISIT, "names": ["Alice"], "face_ids": ["77"]}
        self.assertTrue(_passes(WHO, known, who="named"))
        self.assertFalse(_passes(WHO, VISIT, who="named"))
        self.assertTrue(_passes(WHO, VISIT, who="strangers"))
        self.assertFalse(_passes(WHO, known, who="strangers"))

    def test_anyone_lets_both_through(self):
        known = {**VISIT, "names": ["Alice"]}
        self.assertTrue(_passes(WHO, known, who="anyone"))
        self.assertTrue(_passes(WHO, VISIT, who="anyone"))

    def test_a_face_the_hub_saw_but_you_have_not_named_is_a_stranger(self):
        """`names` is what the integration resolved, and an empty list is what
        "stranger" means -- a face id on its own is still nobody."""
        unnamed = {**VISIT, "face_ids": ["481036337152"], "names": []}
        self.assertTrue(_passes(WHO, unnamed, who="strangers"))


class TheEventCarriesIt(unittest.TestCase):
    """A template reading a key nothing sets is silently false, so a wrong
    payload here would leave the night gate permanently shut with nothing
    anywhere reporting it."""

    CAMERAS = [{"device_id": "cam0", "alias": "Front"}]

    def _fired(self, when, **options):
        clip = {"startTime": when, "endTime": when + 15, "events_1": 0b100010}
        cameras = self.CAMERAS

        class _Client:
            def cameras(self):
                return list(cameras)

            def recent(self, camera, start, end):
                return [dict(clip)]

            detections = recent

            def hub_status(self):
                return {}

        hass = harness._Hass()
        coord = coordinator_mod.H500Coordinator(
            hass, harness._Entry(20, **options), _Client())
        coord._download_new = lambda *a, **k: None
        coord.data = asyncio.run(coord._async_update_data())
        coord._primed = True
        coord._visits = {}
        coord._encounters = []
        coord._note_visits({0: [dict(clip)]})
        return [data for name, data in hass.bus.fired
                if name == component_const.EVENT_VISIT][0]

    @staticmethod
    def _at_hour(hour):
        """A moment at this LOCAL hour, in the harness's -07:00 zone."""
        now = 1_786_600_000
        for back in range(24):
            clips = importlib.import_module("tapo_h500.clips")
            if clips.local_hour(now - back * 3600) == hour:
                return now - back * 3600
        raise AssertionError(f"no {hour}:00 within the window")

    def test_a_visit_after_dark_is_marked(self):
        self.assertIs(self._fired(self._at_hour(2))["night"], True)

    def test_a_visit_in_daylight_is_not(self):
        self.assertIs(self._fired(self._at_hour(15))["night"], False)

    def test_the_hour_is_local_not_utc(self):
        """3pm local is 22:00 UTC in this zone, which is inside the default
        night window -- so a UTC hour would mark every afternoon as night."""
        clips = importlib.import_module("tapo_h500.clips")
        moment = self._at_hour(15)
        self.assertNotEqual(clips.local_hour(moment), (moment // 3600) % 24)
        self.assertIs(self._fired(moment)["night"], False)

    def test_it_follows_the_configured_window(self):
        """Night is configurable because a night shift makes a nonsense of
        anyone else's idea of it."""
        moment = self._at_hour(15)
        self.assertIs(
            self._fired(moment, night_start=14, night_end=16)["night"], True)


class Wording(unittest.TestCase):
    def test_it_names_the_person_when_it_knows_one(self):
        self.assertIn("{{ named }} is at the {{ where }}", RAW)

    def test_it_never_reads_a_face_id_out(self):
        """A twelve-digit number spoken to a room is worse than saying
        nothing."""
        self.assertNotIn("face_ids", RAW)

    def test_it_mentions_the_other_camera_rather_than_repeating_itself(self):
        """Two doorbells watching one path are one arrival. The event already
        merges them; the message says where else it was seen."""
        self.assertIn("trigger.event.data.cameras", RAW)
        self.assertIn("also seen at the", RAW)

    def test_it_uses_the_integration_s_own_phrase_for_what_was_seen(self):
        """Shared with the cards and the digest, so a notification and a
        dashboard cannot describe the same visit differently."""
        self.assertIn("trigger.event.data.detection", RAW)

    def test_speaking_needs_both_halves(self):
        """tts.speak names the engine and the speaker separately, and calling
        it with one missing fails the whole run."""
        self.assertIn("input_tts_entity != '' and input_speaker != ''", RAW)

    def test_the_time_is_local(self):
        self.assertIn("as_local", RAW)


if __name__ == "__main__":
    unittest.main()
