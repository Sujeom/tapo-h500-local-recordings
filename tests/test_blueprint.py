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
        """Not just the headline alarm_type. Found by text rather than by
        position: conditions are now nested under an or/and pair so the reply
        branch can skip them, and an index would break on any restructure."""
        self.assertIn("select('in', wanted)", RAW)
        self.assertIn("detection_types", RAW)
        self.assertNotIn("alarm_type", RAW)


class KnownFaces(unittest.TestCase):
    """A recognised person is named rather than called "a person"."""

    def test_the_sentence_uses_names_not_ids(self):
        """An automation cannot reach the hub's name map, so the integration
        resolves it. face_ids IS read elsewhere -- deciding whether to offer
        the naming button -- so this is scoped to the phrase that reaches a
        person, where a twelve-digit number would be gibberish."""
        who = RAW.split("      who: >-", 1)[1].split("      headline:", 1)[0]
        self.assertIn("'faces'", who)
        self.assertNotIn("face_ids", who)

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


class NamingFromThePhone(unittest.TestCase):
    def test_the_button_carries_the_ids_it_will_need(self):
        """The reply event has no reliable device to derive them from, so they
        travel with the button and come back echoed."""
        self.assertIn("'face_id': unnamed", RAW)
        self.assertIn("'entry': config_entry_id(trigger.entity_id)", RAW)

    def test_the_reply_is_handled_by_the_same_automation(self):
        self.assertIn("mobile_app_notification_action", RAW)
        self.assertIn("action: TAPO_H500_NAME_FACE", RAW)
        self.assertIn("action: tapo_h500.name_face", RAW)

    def test_the_reply_branch_stops_before_the_notification_work(self):
        """A button press is not a detection and must not send an alert."""
        self.assertIn('- stop: "named"', RAW)

    def test_buttons_are_built_as_a_value_not_as_yaml(self):
        """The document is parsed before any template runs, so Jinja cannot
        add or remove keys and list entries -- only fill them in. An {% if %}
        wrapped around a list item makes the file unparseable."""
        self.assertIn('actions: "{{ buttons }}"', RAW)
        self.assertNotIn("{% if input_offer_naming and unnamed %}\n          -", RAW)

    def test_naming_is_only_offered_for_an_unrecognised_face(self):
        self.assertIn("ids[0] if ids and not known else ''", RAW)


class NightEscalation(unittest.TestCase):
    def test_the_integration_decides_what_counts_as_night(self):
        """Not the blueprint: a window that wraps midnight is the obvious
        thing to get wrong, and it is already solved in one place."""
        self.assertIn("state_attr(trigger.entity_id, 'notable')", RAW)

    def test_a_notable_alert_sounds_different(self):
        """Marking it without changing the channel changes nothing on a
        phone, which is the entire point."""
        self.assertIn("'Tapo H500 alerts' if notable", RAW)
        self.assertIn("'high' if notable", RAW)


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


class FirstEventAfterRestart(unittest.TestCase):
    """The availability guard must not swallow the first real press.

    An event entity sits at `unknown` after every reload and restart, so the
    guard's old form -- from_state AND to_state both valid -- dropped the
    first genuine event afterwards: rang the bell, no notification, works the
    second time. What the from_state half was actually protecting against is
    the entity's restored state replaying an OLD event on startup, and the
    difference between those two cases is the event's own timestamp: a real
    press is seconds old, a restore replay is hours old.

    Rendered for real with HA's now()/as_timestamp stubbed, because a guard
    that is only grepped for can be inverted without a test noticing.
    """

    NOW = 1_786_600_000.0

    @staticmethod
    def _detection_filter(seen, wanted):
        """Render the detection-type condition exactly as HA would."""
        import types
        import jinja2
        template = next(
            inner["value_template"]
            for outer in DOC["conditions"] for inner in
            outer.get("conditions", [{}])[-1].get("conditions", [])
            if "detection_types" in inner.get("value_template", ""))
        env = jinja2.Environment()  # noqa: S701 - not HTML
        rendered = env.from_string(template).render(
            input_detections=wanted,
            trigger=types.SimpleNamespace(entity_id="event.front_activity"),
            state_attr=lambda entity, attribute: seen)
        return rendered.strip() == "True"

    def test_an_unclassified_event_still_notifies(self):
        """The real failure, from a trace on 2026-08-27: the hub indexes a
        clip before its detection log catches up, so the event fires with
        `detection_types: []` -- real timing, real picture, no classification
        yet. The old filter asked "does this list intersect the wanted one",
        which an empty list never can, so the visitor was dropped outright and
        no selection of codes could ever have recovered them.
        """
        self.assertTrue(self._detection_filter([], ["17", "6", "9"]))
        self.assertTrue(self._detection_filter(None, ["17", "6", "9"]))

    def test_a_classified_event_is_still_filtered(self):
        """The half that must not regress. Passing the unclassified case is
        only correct while a populated list is still held to the selection --
        otherwise this stops being a filter at all."""
        self.assertFalse(self._detection_filter([2, 8], ["17", "6", "9"]))
        self.assertTrue(self._detection_filter([2, 6], ["17", "6", "9"]))
        self.assertTrue(self._detection_filter([2, 22], ["22"]))

    def _passes(self, from_state, to_state):
        import datetime, types
        import jinja2
        env = jinja2.Environment()  # noqa: S701 - not HTML
        moment = datetime.datetime.fromtimestamp(
            self.NOW, datetime.timezone.utc)
        env.globals["now"] = lambda: moment
        env.filters["as_timestamp"] = lambda value, default=None: (
            datetime.datetime.fromisoformat(value).timestamp()
            if isinstance(value, str) and value not in
            ("unknown", "unavailable") else default)
        template = next(
            inner["value_template"]
            for outer in DOC["conditions"] for inner in
            outer.get("conditions", [{}])[-1].get("conditions", [])
            if "to_state" in inner.get("value_template", ""))
        trigger = types.SimpleNamespace(
            from_state=(None if from_state is None
                        else types.SimpleNamespace(state=from_state)),
            to_state=types.SimpleNamespace(state=to_state))
        rendered = env.from_string(template).render(trigger=trigger)
        return rendered.strip() == "True"

    def _iso(self, seconds_ago):
        import datetime
        return datetime.datetime.fromtimestamp(
            self.NOW - seconds_ago, datetime.timezone.utc).isoformat()

    def test_the_first_press_after_a_restart_notifies(self):
        self.assertTrue(self._passes("unknown", self._iso(3)))

    def test_a_restored_old_event_stays_silent(self):
        """The case the guard existed for, still guarded."""
        self.assertFalse(self._passes("unknown", self._iso(7200)))

    def test_a_normal_event_still_notifies(self):
        self.assertTrue(self._passes(self._iso(600), self._iso(3)))

    def test_a_revision_of_a_recent_event_still_notifies(self):
        """Motion revised to motion+press keeps the same start time, so the
        to_state can be a minute old on a perfectly normal transition."""
        self.assertTrue(self._passes(self._iso(600), self._iso(90)))

    def test_going_unavailable_never_notifies(self):
        self.assertFalse(self._passes(self._iso(600), "unavailable"))
        self.assertFalse(self._passes(self._iso(600), "unknown"))

    def test_a_brand_new_entity_notifies_for_a_fresh_event(self):
        self.assertTrue(self._passes(None, self._iso(3)))

    def test_the_other_blueprint_and_the_example_carry_the_same_guard(self):
        """One fix, three files; drift between them is how the bug returns."""
        mine = next(
            inner["value_template"]
            for outer in DOC["conditions"] for inner in
            outer.get("conditions", [{}])[-1].get("conditions", [])
            if "to_state" in inner.get("value_template", ""))
        for name in ("../blueprints/automation/tapo_h500/respond_to_activity.yaml",
                     "../examples/notify-person-pet-doorbell.yaml"):
            text = (Path(__file__).parent / name).read_text()
            self.assertIn(" ".join(mine.split())[:80],
                          " ".join(text.split()), name)


class SaveClipButton(unittest.TestCase):
    """Press Save on the photo notification and the clip is kept.

    Manual downloads are never pruned, so this is "keep that one forever"
    from the phone, through the same event round trip the naming button
    uses. Photo notification only: at first-notification time the hub is
    usually still recording and there is nothing indexed to save.
    """

    def test_the_reply_trigger_exists(self):
        actions = [trigger.get("event_data", {}).get("action")
                   for trigger in DOC["triggers"]]
        self.assertIn("TAPO_H500_SAVE_CLIP", actions)

    def test_the_reply_skips_the_detection_conditions(self):
        first = DOC["conditions"][0]["conditions"]
        ids = [inner.get("id") for inner in first
               if inner.get("condition") == "trigger"]
        self.assertIn("saving", ids)

    def test_the_reply_downloads_that_exact_clip(self):
        branch = next(step for step in DOC["actions"]
                      if "saving" in str(step.get("if", "")))
        body = str(branch["then"])
        self.assertIn("tapo_h500.download_recording", body)
        self.assertIn("start_time", body)
        self.assertIn("camera_index", body)
        # And stops: none of the notification work below applies.
        self.assertIn("stop", body)

    def test_it_does_not_guess_an_end_time(self):
        """The detection log has no end; the service looks it up."""
        branch = next(step for step in DOC["actions"]
                      if "saving" in str(step.get("if", "")))
        self.assertNotIn("end_time", str(branch["then"]))

    def test_the_photo_notification_offers_it(self):
        photo = str(DOC["actions"][-1])
        self.assertIn("photo_buttons", photo)
        variables = str(DOC["actions"])
        self.assertIn("TAPO_H500_SAVE_CLIP", variables)
        self.assertIn("Save clip", variables)

    def test_the_first_notification_does_not(self):
        """Nothing is indexed yet; a button that always fails teaches people
        to ignore buttons."""
        first = next(step for step in DOC["actions"]
                     if step.get("action") == "{{ service }}")
        self.assertNotIn("photo_buttons", str(first))

    def test_the_button_carries_what_the_service_needs(self):
        variables = str(DOC["actions"])
        save = variables.split("TAPO_H500_SAVE_CLIP", 2)[-1][:400]
        for key in ("entry", "camera_index", "start_time"):
            self.assertIn(key, save)


class CameraIndexAttribute(unittest.TestCase):
    def test_the_event_entity_says_which_camera_it_is(self):
        """The Save button needs the service's camera_index, and an
        automation cannot derive a paired-list position from an entity id."""
        event_src = (ROOT / "custom_components" / "tapo_h500"
                     / "event.py").read_text()
        self.assertIn('"camera_index": self.index', event_src)


class SnoozeButton(unittest.TestCase):
    """Quiet for an hour, from the notification that interrupted you.

    The moment somebody wants a snooze is the moment the phone is buzzing,
    not later in a dashboard. Same round trip as naming and saving; calls
    the service the snooze switch already exposes, so recording never
    stops and the automation stays enabled.
    """

    def test_the_reply_trigger_exists(self):
        actions = [trigger.get("event_data", {}).get("action")
                   for trigger in DOC["triggers"]]
        self.assertIn("TAPO_H500_SNOOZE", actions)

    def test_the_reply_skips_the_detection_conditions(self):
        first = DOC["conditions"][0]["conditions"]
        ids = [inner.get("id") for inner in first
               if inner.get("condition") == "trigger"]
        self.assertIn("snoozing", ids)

    def test_the_reply_snoozes_for_an_hour_and_stops(self):
        branch = next(step for step in DOC["actions"]
                      if "snoozing" in str(step.get("if", "")))
        body = str(branch["then"])
        self.assertIn("tapo_h500.snooze", body)
        self.assertIn("'minutes': 60", body)
        self.assertIn("stop", body)

    def test_both_notifications_offer_it(self):
        """Unlike Save clip, snoozing needs no indexed recording, so the
        first, instant notification carries it too."""
        buttons = str(DOC["actions"])
        self.assertIn("TAPO_H500_SNOOZE", buttons.split("photo_buttons")[0])

    def test_it_carries_the_entry_the_service_needs(self):
        variables = str(DOC["actions"])
        snooze = variables.split("TAPO_H500_SNOOZE", 1)[1][:300]
        self.assertIn("entry", snooze)


class ButtonBudget(unittest.TestCase):
    """Android shows at most three notification actions; a fourth vanishes.

    Rendered for real: the worst case is an unnamed face, where the naming
    button joins in, and both notifications must still fit the budget.
    """

    def _buttons(self, which, unnamed):
        import types
        import jinja2
        env = jinja2.Environment()  # noqa: S701 - not HTML
        env.globals["config_entry_id"] = lambda entity: "entry1"
        env.globals["state_attr"] = lambda entity, name: 0
        variables = {}
        for step in DOC["actions"]:
            if "variables" in step:
                variables = step["variables"]
        base = env.from_string(variables["buttons"]).render(
            input_offer_naming=True,
            unnamed="12345" if unnamed else "",
            link="/x",
            trigger=types.SimpleNamespace(entity_id="event.front"))
        buttons = eval(base)  # noqa: S307 - our own template's output
        if which == "photo":
            photo = env.from_string(variables["photo_buttons"]).render(
                buttons=buttons, moment=1,
                trigger=types.SimpleNamespace(entity_id="event.front"))
            return eval(photo)  # noqa: S307
        return buttons

    def test_neither_notification_exceeds_three_actions(self):
        for which in ("first", "photo"):
            for unnamed in (False, True):
                buttons = self._buttons(which, unnamed)
                self.assertLessEqual(
                    len(buttons), 3,
                    f"{which} notification with unnamed={unnamed} offers "
                    f"{[b['title'] for b in buttons]}")

    def test_the_photo_notification_swaps_snooze_for_save(self):
        titles = [button["title"] for button in self._buttons("photo", False)]
        self.assertIn("Save clip", titles)
        self.assertNotIn("Snooze 1h", titles)

    def test_the_first_notification_offers_snooze(self):
        titles = [button["title"] for button in self._buttons("first", False)]
        self.assertIn("Snooze 1h", titles)


class QuietHours(unittest.TestCase):
    """No notifications between two clock times -- unless it matters.

    Rendered for real with now() stubbed, wrap-around included, because a
    window crossing midnight is exactly where a grepped-for template goes
    wrong. `notable` (tampering any time, an unfamiliar face at night)
    punches through: the whole point of a loud channel is that quiet hours
    do not apply to it.
    """

    def _passes(self, clock, start, end, notable=False):
        import datetime
        import types
        import jinja2
        env = jinja2.Environment()  # noqa: S701 - not HTML
        hour, minute = map(int, clock.split(":"))
        env.globals["now"] = lambda: datetime.datetime(
            2026, 8, 19, hour, minute)
        template = next(
            inner["value_template"]
            for outer in DOC["conditions"] for inner in
            outer.get("conditions", [{}])[-1].get("conditions", [])
            if "quiet_start" in inner.get("value_template", ""))
        rendered = env.from_string(template).render(
            input_quiet_start=start, input_quiet_end=end,
            notable=notable)
        return rendered.strip() == "True"

    def test_unset_means_always_notify(self):
        self.assertTrue(self._passes("03:00", "", ""))

    def test_inside_the_window_is_silent(self):
        self.assertFalse(self._passes("23:30", "22:00", "07:00"))
        self.assertFalse(self._passes("03:00", "22:00", "07:00"))

    def test_outside_the_window_notifies(self):
        self.assertTrue(self._passes("12:00", "22:00", "07:00"))
        self.assertTrue(self._passes("21:59", "22:00", "07:00"))
        self.assertTrue(self._passes("07:00", "22:00", "07:00"))

    def test_a_window_inside_one_day_works_too(self):
        self.assertFalse(self._passes("14:00", "13:00", "15:00"))
        self.assertTrue(self._passes("16:00", "13:00", "15:00"))

    def test_notable_punches_through(self):
        self.assertTrue(self._passes("03:00", "22:00", "07:00", notable=True))

    def test_the_inputs_exist_with_time_selectors(self):
        for name in ("quiet_start", "quiet_end"):
            self.assertIn("time", str(DOC["blueprint"]["input"][name]))
