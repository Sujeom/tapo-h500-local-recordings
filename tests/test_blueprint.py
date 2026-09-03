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


class ThisEventsOwnClip(unittest.TestCase):
    """`moment` is the clip's start time, and both consumers depend on it.

    An event entity's state is the instant `_trigger_event` ran, which is the
    poll that noticed the recording -- seconds to minutes after the recording
    itself began. `sensor.<cam>_last_activity` holds clip START times
    (`coordinator.last_activity` is `max(start_of(clip) ...)`), so a wait for
    that sensor to pass the event's FIRE time is a wait for a clip only the
    next visitor can produce: three minutes, then silence.

    Rendered for real, because the two timestamps are both plausible integers
    and a grepped-for template cannot tell them apart.
    """

    CLIP = 1_786_600_000              # when the recording began
    FIRED = CLIP + 40                 # when the poll noticed it and fired
    PREVIOUS = CLIP - 600             # the visitor before this one

    WAIT = next(line.strip() for line in RAW.splitlines()
                if "states(activity)" in line and ">= moment" in line)

    @staticmethod
    def _iso(moment):
        """A timestamp sensor's state: ISO 8601, UTC, as HA writes it."""
        import datetime
        return datetime.datetime.fromtimestamp(
            moment, datetime.timezone.utc).isoformat()

    def _env(self):
        """HA's `as_timestamp`, which answers its default for a state that is
        not a time -- `unknown` after a restart, most of all."""
        import datetime
        import jinja2
        env = jinja2.Environment()  # noqa: S701 - not HTML

        def as_timestamp(value, default=None):
            try:
                return datetime.datetime.fromisoformat(value).timestamp()
            except (TypeError, ValueError):
                return default

        env.filters["as_timestamp"] = as_timestamp
        env.globals["as_timestamp"] = as_timestamp
        return env

    def _variables(self):
        return next(step["variables"] for step in DOC["actions"]
                    if "variables" in step)

    def _moment(self, start_time):
        """The blueprint's own `moment`, rendered and given the native type
        Home Assistant gives a variable."""
        import types
        rendered = self._env().from_string(self._variables()["moment"]).render(
            trigger=types.SimpleNamespace(
                entity_id="event.front_doorbell_activity",
                to_state=types.SimpleNamespace(state=self._iso(self.FIRED))),
            state_attr=lambda entity, name: start_time)
        return int(rendered)

    def _waited_out(self, sensor, moment):
        """True when the wait at the photo step is satisfied."""
        env = self._env()
        env.globals["states"] = lambda entity: sensor
        rendered = env.from_string(self.WAIT).render(
            activity="sensor.front_doorbell_last_activity", moment=moment)
        return rendered.strip() == "True"

    def test_the_wait_is_satisfied_by_this_events_own_clip(self):
        """The defect, stated as the thing that has to be true: the hub has
        indexed this event's clip, so the photograph is due now."""
        self.assertTrue(
            self._waited_out(self._iso(self.CLIP), self._moment(self.CLIP)),
            "the photo step is still waiting for a clip this event never "
            "produced, and will time out in silence")

    def test_a_later_clip_alone_is_not_what_satisfies_it(self):
        """The half that must not regress. A `moment` low enough to be true
        immediately would be no wait at all: while the sensor still holds the
        PREVIOUS visitor's clip start, nothing has arrived for this event and
        the picture on disk is that visitor's."""
        self.assertFalse(
            self._waited_out(self._iso(self.PREVIOUS), self._moment(self.CLIP)))

    def test_the_save_button_asks_for_the_clips_own_start_time(self):
        """`moment`'s other consumer. Save clip hands `start_time` straight to
        tapo_h500.download_recording, which looks the clip up in the hub's own
        index -- a fire time matches nothing there."""
        import types
        env = self._env()
        env.globals["config_entry_id"] = lambda entity: "entry1"
        env.globals["state_attr"] = lambda entity, name: 0
        rendered = env.from_string(self._variables()["photo_buttons"]).render(
            buttons=[{"action": "TAPO_H500_SNOOZE", "title": "Snooze 1h"}],
            moment=self._moment(self.CLIP), input_photo_only=False,
            trigger=types.SimpleNamespace(
                entity_id="event.front_doorbell_activity"))
        save = next(button for button in eval(rendered)  # noqa: S307
                    if button["action"] == "TAPO_H500_SAVE_CLIP")
        self.assertEqual(save["start_time"], self.CLIP)

    def test_a_missing_start_time_does_not_block_the_notification(self):
        """The hub can index a clip whose start it does not report. Lenient on
        purpose: 0 satisfies the wait at once and the run carries on to the
        `frame not in [none, '', 'None']` guard, which is the thing that knows
        whether there is a picture. A sentinel that never compares true would
        turn a missing attribute into permanent silence -- the failure being
        fixed, not a fix for it."""
        self.assertEqual(self._moment(None), 0)
        self.assertTrue(self._waited_out("unknown", self._moment(None)))


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

    @staticmethod
    def _message(who, seen):
        """Render the notification body exactly as HA would."""
        import jinja2
        line = next(l.strip() for l in RAW.splitlines()
                    if "{{ when }}" in l and "seen" in l)
        return jinja2.Environment().from_string(line).render(  # noqa: S701
            who=who, seen=seen, when="10:11 AM").strip()

    def test_an_unclassified_event_reads_as_a_sentence(self):
        """The unclassified event has no description, and the message used to
        interpolate it anyway: "Activity at Side Doorbell" / ", 10:11 AM".
        A leading comma is how a notification announces that the automation
        that sent it is broken."""
        self.assertEqual(self._message("", ""), "10:11 AM")
        self.assertNotIn(",", self._message("", ""))

    def test_a_described_event_still_reads_normally(self):
        self.assertEqual(self._message("", "a person"), "a person, 10:11 AM")
        self.assertEqual(self._message("Alice", "a doorbell press"),
                         "Alice — a doorbell press, 10:11 AM")

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


def _branch_holding(needle: str) -> dict:
    """The if/then step whose body mentions `needle`."""
    return next(step for step in DOC["actions"]
                if "if" in step and needle in str(step.get("then")))


def _instant_alert() -> dict:
    """The notification sent the moment a detection lands."""
    branch = _branch_holding("{{ service }}")
    return next(step for step in branch["then"]
                if step.get("action") == "{{ service }}")


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
        to ignore buttons.

        Found inside its branch rather than at the top: the instant alert is
        skipped entirely when only photographed notifications were asked
        for, so it lives under an `if`.
        """
        self.assertNotIn("photo_buttons", str(_instant_alert()))

    def test_the_button_carries_what_the_service_needs(self):
        variables = str(DOC["actions"])
        save = variables.split("TAPO_H500_SAVE_CLIP", 2)[-1][:400]
        for key in ("entry", "camera_index", "start_time"):
            self.assertIn(key, save)


class OnlyNotifyWithAPhotograph(unittest.TestCase):
    """The setting for somebody who never wants a notification without one.

    Two notifications per detection is the default because the first arrives
    in about a second and the hub is still recording; one notification, later,
    is a real preference and this is where it is expressed.
    """

    def test_the_option_exists_and_is_off_by_default(self):
        """On by default would silently delay every doorbell press for
        everybody who already had this blueprint."""
        option = DOC["blueprint"]["input"]["photo_only"]
        self.assertIs(option["default"], False)
        self.assertIn("boolean", option["selector"])

    def test_it_says_what_it_costs(self):
        """Silence is indistinguishable from nothing having happened, so
        the description has to say that is what you are choosing."""
        said = DOC["blueprint"]["input"]["photo_only"]["description"].lower()
        self.assertIn("no notification at all", said)
        self.assertIn("seconds later", said)

    def test_the_instant_alert_is_skipped_when_it_is_on(self):
        guard = str(_branch_holding("{{ service }}")["if"])
        self.assertIn("not input_photo_only", guard)

    def test_the_photograph_is_still_sent_when_it_is_on(self):
        """Turning it on while the follow-up is off would otherwise mean no
        notification ever, which nobody is asking for."""
        photo = _branch_holding("photo_buttons")
        self.assertIn("input_send_photo or input_photo_only",
                      str(photo["if"]))

    def test_snooze_comes_back_when_it_is_the_only_notification(self):
        """Nobody was offered it on a first alert that never happened, and
        there is a free slot exactly when there is no face to name."""
        variables = str(DOC["actions"])
        self.assertIn("input_photo_only and full | count < 3", variables)


class ThePictureUrlResolves(unittest.TestCase):
    """`image` addresses the downloaded file and does not check it exists.

    It 404s for every clip that was never downloaded and for every clip at
    all until the hub stops recording, so a notification using it shows an
    empty picture with nothing to tell that apart from one still on its way.
    """

    def test_the_preview_is_preferred(self):
        variables = str(DOC["actions"])
        self.assertIn("state_attr(trigger.entity_id, 'preview')", variables)

    def test_and_the_file_is_the_fallback(self):
        """So the blueprint still works against a version of the integration
        that has no preview attribute."""
        frame = str(DOC["actions"]).split("'preview')", 1)[1][:120]
        self.assertIn("'image'", frame)

    def test_the_preview_comes_first(self):
        variables = str(DOC["actions"])
        self.assertLess(variables.index("'preview'"),
                        variables.index("state_attr(trigger.entity_id, 'image')"))


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
        # The worst case: both files are there to open AND a face wants
        # naming, so Image, Video, Name this face and Snooze all compete.
        base = env.from_string(variables["buttons"]).render(
            input_offer_naming=True,
            unnamed="12345" if unnamed else "",
            link="/x", camera="camera.front", moment=1786600000, dashboard="0",
            picture_entity="image.front_latest_event",
            trigger=types.SimpleNamespace(entity_id="event.front"))
        buttons = eval(base)  # noqa: S307 - our own template's output
        if which == "photo":
            photo = env.from_string(variables["photo_buttons"]).render(
                buttons=buttons, moment=1, input_photo_only=False,
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

    def test_the_photo_notification_keeps_image_and_video(self):
        """Image opens the picture's dialog, full size and inside the app,
        which the thumbnail on the notification is not. Reported: only a
        Video button on the notification that actually gets looked at."""
        titles = [button["title"] for button in self._buttons("photo", False)]
        self.assertEqual(titles, ["Image", "Video", "Save clip"])

    def test_the_only_notification_keeps_snooze_over_save(self):
        """A recording can be kept from the card later; a night's quiet
        cannot be asked for later."""
        import types
        import jinja2
        env = jinja2.Environment()  # noqa: S701 - not HTML
        env.globals["config_entry_id"] = lambda entity: "entry1"
        env.globals["state_attr"] = lambda entity, name: 0
        variables = next(s["variables"] for s in DOC["actions"] if "variables" in s)
        buttons = [{"action": "URI", "title": "Image", "uri": "entityId:image.x"},
                   {"action": "URI", "title": "Video", "uri": "/lovelace/0?h500_play=1"},
                   {"action": "TAPO_H500_SNOOZE", "title": "Snooze 1h"}]
        photo = env.from_string(variables["photo_buttons"]).render(
            buttons=buttons, moment=1, input_photo_only=True,
            trigger=types.SimpleNamespace(entity_id="event.front"))
        self.assertEqual([b["title"] for b in eval(photo)],  # noqa: S307
                         ["Image", "Video", "Snooze 1h"])


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


def _variables():
    """The blueprint's declared variables, in declaration order."""
    for step in DOC["actions"]:
        if isinstance(step, dict) and "variables" in step:
            return step["variables"]
    raise AssertionError("the blueprint declares no variables")


VARIABLES = _variables()


class TheCameraButtonAddressesSomethingReal(unittest.TestCase):
    """The Camera button pointed wherever spelling produced, checked by nobody.

    `event.side_door_activity` was rewritten into `camera.side_door` and
    handed straight to `?more-info-entity-id=`. Rename either entity, let
    Home Assistant append a `_2` to one of them, or have no camera entity at
    all -- which is what a camera the hub has stopped listing looks like --
    and the id named nothing. An unknown entity is not an error to the
    frontend: it opens the dashboard and no dialog, so the button reads as
    dead. Every entity for a camera is built by H500Entity with the same
    `identifiers={(DOMAIN, camera["device_id"])}`, so the device registry
    answers this without spelling anything.
    """

    EVENT = "event.side_door_activity"
    GUESS = "camera.side_door"          # what rewriting the id produced
    REAL = "camera.front_doorbell"      # what is actually on the device
    STILL = "/media/local/tapo_h500/side_doorbell/2026-09-01/235339.jpg"
    CLIP = "/media/local/tapo_h500/side_doorbell/2026-09-01/235339.mp4"

    def _camera(self, entities, device="dev_1"):
        import jinja2
        import types
        env = jinja2.Environment()  # noqa: S701 - not HTML
        env.tests["search"] = lambda value, pattern: bool(
            re.search(pattern, value))
        env.globals["device_id"] = lambda entity: device
        env.globals["device_entities"] = lambda dev: (
            entities if dev == device else [])
        return env.from_string(VARIABLES["camera"]).render(
            trigger=types.SimpleNamespace(entity_id=self.EVENT)).strip()

    def _link(self, camera, dashboard="0"):
        import jinja2
        return jinja2.Environment().from_string(  # noqa: S701 - not HTML
            VARIABLES["link"]).render(
                camera=camera, dashboard=dashboard).strip()

    def _buttons(self, camera, frame, clip="", moment=0, dashboard="0",
                 picture_entity="", link="/lovelace/0?more-info-entity-id=x"):
        import ast
        import jinja2
        import types
        rendered = jinja2.Environment().from_string(  # noqa: S701 - not HTML
            VARIABLES["buttons"]).render(
                camera=camera, frame=frame, clip=clip, link=link,
                moment=moment, dashboard=dashboard, picture_entity=picture_entity,
                state_attr=lambda entity, name: 1,
                input_offer_naming=False, unnamed="",
                config_entry_id=lambda entity: "entry1",
                trigger=types.SimpleNamespace(entity_id=self.EVENT))
        return ast.literal_eval(rendered.strip())

    def _uris(self, buttons):
        return [b["uri"] for b in buttons if b["action"] == "URI"]

    def test_the_camera_on_this_events_device_is_the_one_offered(self):
        """The pairing lives in the device registry, not in the spelling."""
        found = self._camera([self.EVENT, self.REAL, "sensor.side_door_silent"])
        self.assertEqual(found, self.REAL)

    def test_the_rewritten_id_is_not_what_gets_used(self):
        """The whole defect: `camera.side_door` is not on this device and is
        not an entity at all, but it is what the button used to address."""
        found = self._camera([self.EVENT, self.REAL])
        self.assertNotEqual(found, self.GUESS)

    def test_a_device_with_no_camera_entity_resolves_to_nothing(self):
        """A camera the hub has stopped listing has no camera entity, and
        there is then nothing for a dialog to open."""
        self.assertEqual(self._camera([self.EVENT, "sensor.side_door_silent"]),
                         "")

    def test_an_entity_merely_mentioning_camera_is_not_one(self):
        """`^camera[.]` is anchored on the domain, so a binary sensor about
        the camera is not mistaken for the camera."""
        self.assertEqual(self._camera([self.EVENT, "binary_sensor.camera_tamper"]),
                         "")

    def test_an_entity_with_no_device_resolves_to_nothing(self):
        """`device_entities(None)` raises rather than answering empty."""
        self.assertEqual(self._camera([self.REAL], device=None), "")

    def test_the_link_opens_the_dialog_when_there_is_a_camera(self):
        self.assertEqual(self._link(self.REAL),
                         f"/lovelace/0?more-info-entity-id={self.REAL}")

    def test_the_link_asks_for_no_dialog_when_there_is_no_camera(self):
        """A dangling `more-info-entity-id=` is a dashboard and no dialog --
        exactly what a broken button looks like."""
        self.assertEqual(self._link(""), "/lovelace/0")
        self.assertNotIn("more-info-entity-id", self._link(""))

    MOMENT = 1786600000

    def _every_combination(self):
        for camera in (self.REAL, ""):
            for frame in ("/api/preview/1", ""):
                for moment in (self.MOMENT, 0):
                    yield self._buttons(camera, frame, self.CLIP, moment)

    def test_every_button_stays_inside_the_app(self):
        """The rule, from the Android app's own source: a relative URI is
        loaded in the app's own frontend webview. A media path there is
        broken by the `external_auth=1` the app appends, and an absolute URL
        is handed to the system browser -- so neither may be a button."""
        for buttons in self._every_combination():
            for uri in self._uris(buttons):
                self.assertTrue(uri.startswith(("/lovelace/", "entityId:"))
                                or uri == "/media-browser",
                                f"{uri} does not stay in the app")
                self.assertFalse("/media/" in uri or "/api/" in uri, uri)

    def test_image_and_video_land_on_this_clip_in_the_dashboard_view(self):
        buttons = [b for b in self._buttons(self.REAL, "", self.CLIP, self.MOMENT, "cams")
                   if b["action"] == "URI"]
        self.assertEqual([b["title"] for b in buttons], ["Image", "Video"])
        image, video = (b["uri"] for b in buttons)
        self.assertEqual(image, f"/lovelace/cams?h500_clip={self.MOMENT}&h500_camera=1")
        self.assertEqual(video, image + "&h500_play=1")

    def test_image_opens_the_picture_entitys_dialog_when_there_is_one(self):
        """Tested on the phone: `entityId:` is the one route that lands in
        the app's own more-info dialog, and it needs no dashboard set up."""
        buttons = [b for b in self._buttons(self.REAL, "", self.CLIP, self.MOMENT,
                                            picture_entity="image.side_latest_event")
                   if b["action"] == "URI"]
        self.assertEqual([b["title"] for b in buttons], ["Image", "Video"])
        self.assertEqual(buttons[0]["uri"], "entityId:image.side_latest_event")
        self.assertTrue(buttons[1]["uri"].endswith("&h500_play=1"),
                        "video has no dialog to open and takes the view")

    def test_the_dialog_needs_no_clip_start(self):
        """An integration that publishes the entity but an event without a
        start time: the dialog still opens; only Video has nothing to name."""
        buttons = self._buttons(self.REAL, "", "", 0, picture_entity="image.x")
        self.assertEqual(self._uris(buttons), ["entityId:image.x"])

    def test_the_query_survives_the_apps_url_handling(self):
        """The app decodes the path and re-splits it on '/', which is what
        shredded an encoded media-browser link. Digits and '&' in a query
        string pass through untouched, so nothing here may need encoding."""
        for b in self._buttons(self.REAL, "", self.CLIP, self.MOMENT):
            if b["action"] == "URI" and "?" in b["uri"]:
                query = b["uri"].split("?", 1)[1]
                self.assertRegex(query, r"^[a-z0-9_=&]+$", query)

    def test_recordings_fills_in_when_there_is_no_clip_to_name(self):
        """An event without a start time, or an integration too old to
        publish one: the frontend route still lands on the recordings."""
        self.assertEqual(self._uris(self._buttons(self.REAL, "")),
                         ["/lovelace/0?more-info-entity-id=x"])
        self.assertEqual(self._uris(self._buttons("", "")), ["/media-browser"])

    def test_three_actions_is_the_ceiling(self):
        """Android shows three."""
        for buttons in self._every_combination():
            self.assertLessEqual(len(buttons), 3)

    def test_the_recording_rides_the_notification(self):
        """`image` has always worked because the app fetches an attachment
        with its own token. `video` is the same channel."""
        photo = _branch_holding("photo_buttons")
        self.assertIn('"video": "{{ clip }}"', str(photo).replace("'", '"'))
