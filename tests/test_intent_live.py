"""The three Assist intents, asked out loud.

"What was the last thing the cameras saw", "what happened today", and "snooze
the notifications" -- each spoken answer is built here and nowhere else, so a
wrong sentence is this module's fault. 94 lines at 35% coverage, tested only
by reading the source.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

intent_mod = importlib.import_module("tapo_h500.intent")
dt_util = sys.modules["homeassistant.util.dt"]
NOW = int(dt_util.utcnow().timestamp())


def clip(start, mask=1 << 1):
    return {"startTime": start, "endTime": start + 15, "events_1": mask}


class _Response:
    def __init__(self):
        self.speech = None

    def async_set_speech(self, text):
        self.speech = text


class _Request:
    def __init__(self, hass):
        self.hass = hass

    def create_response(self):
        return _Response()


def _hub(alias_clips, title="Tapo H500 (11.5)"):
    coord, _ = harness._build()
    coord.entry.title = title
    coord.cameras = [{"device_id": f"d{n}", "alias": alias}
                     for n, alias in enumerate(alias_clips)]
    coord.clips_for = lambda index: list(
        alias_clips[coord.cameras[index]["alias"]])
    return coord


def ask(handler, *coords):
    hass = harness._Hass()
    hass.data = {"tapo_h500": {"hubs": {
        f"entry{n}": coord for n, coord in enumerate(coords)}}}
    return asyncio.run(handler.async_handle(_Request(hass))).speech


class SpokenAges(unittest.TestCase):
    def test_rounded_the_way_a_person_says_it(self):
        """"4,133 seconds ago" is not an answer."""
        for seconds, said in ((30, "just now"), (89, "just now"),
                              (120, "2 minutes ago"), (3600, "1 hour ago"),
                              (7200, "2 hours ago"), (86400, "1 day ago"),
                              (200000, "2 days ago")):
            with self.subTest(seconds):
                self.assertEqual(intent_mod._ago(seconds), said)


class TheLastEvent(unittest.TestCase):
    def test_the_newest_clip_across_every_hub_wins(self):
        first = _hub({"Front": [clip(NOW - 3600)]})
        second = _hub({"Shed": [clip(NOW - 120)]}, title="Barn hub")
        spoken = ask(intent_mod.LastEventIntent(), first, second)
        self.assertEqual(spoken, "motion at the Shed, 2 minutes ago.")

    def test_a_quiet_day_is_said_plainly(self):
        spoken = ask(intent_mod.LastEventIntent(), _hub({"Front": []}))
        self.assertEqual(spoken, "Nothing has been recorded in the last day.")

    def test_what_fired_is_the_decoded_detection(self):
        spoken = ask(intent_mod.LastEventIntent(),
                     _hub({"Front": [clip(NOW - 60, (1 << 1) | (1 << 5))]}))
        self.assertIn("motion + person", spoken)


class Today(unittest.TestCase):
    def test_no_cameras_is_its_own_sentence(self):
        coord = _hub({})
        self.assertEqual(ask(intent_mod.TodayIntent(), coord),
                         "No cameras are set up.")

    def test_an_ordinary_day_counts_each_camera(self):
        spoken = ask(intent_mod.TodayIntent(),
                     _hub({"Front": [clip(NOW - 600), clip(NOW - 300)],
                           "Side": [clip(NOW - 900)]}))
        self.assertIn("Front", spoken)
        self.assertIn("Side", spoken)

    def test_what_was_different_is_said_first(self):
        """Spoken aloud, a bare list of totals is the same sentence every
        day; the silent camera leads."""
        spoken = ask(intent_mod.TodayIntent(),
                     _hub({"Front": [clip(NOW - 600)], "Side": []}))
        self.assertTrue(spoken.startswith("Side recorded nothing"), spoken)

    def test_two_hubs_sharing_a_camera_name_drop_neither(self):
        """Two "Front Doorbell"s into a dictionary silently keeps one; the
        answer then describes half the house as though it were all of it."""
        one = _hub({"Front": [clip(NOW - 600)]}, title="House")
        two = _hub({"Front": [clip(NOW - 300)]}, title="Annex")
        spoken = ask(intent_mod.TodayIntent(), one, two)
        self.assertIn("House", spoken)
        self.assertIn("Annex", spoken)


class Snooze(unittest.TestCase):
    def test_every_hub_goes_quiet_for_an_hour(self):
        one, two = _hub({"Front": []}), _hub({"Shed": []})
        spoken = ask(intent_mod.SnoozeIntent(), one, two)
        self.assertIn("snoozed for an hour", spoken)
        self.assertIn("Recording continues", spoken)
        self.assertEqual(one.snoozed_until, NOW + 3600)
        self.assertEqual(two.snoozed_until, NOW + 3600)

    def test_no_hub_at_all_is_not_a_false_promise(self):
        spoken = ask(intent_mod.SnoozeIntent())
        self.assertEqual(spoken, "No Tapo hub is set up.")


class Registration(unittest.TestCase):
    def test_all_three_are_registered(self):
        registered = []
        intent_helper = intent_mod.intent
        original = intent_helper.async_register
        intent_helper.async_register = (
            lambda hass, handler: registered.append(handler))
        try:
            asyncio.run(intent_mod.async_setup_intents(harness._Hass()))
        finally:
            intent_helper.async_register = original
        self.assertEqual({type(h).__name__ for h in registered},
                         {"LastEventIntent", "TodayIntent", "SnoozeIntent"})


class TheSpokenAndTheWrittenAnswer(unittest.TestCase):
    """The digest service and the voice answer describe the same day.

    They are separate code paths reading the same clips, and the point of
    routing both through summarise() is that a household hears the sentence
    it would have read. Two phrasings drifting apart is exactly the failure
    that reads as fine in review -- both look correct on their own.
    """

    def setUp(self):
        self.services = importlib.import_module("tapo_h500.services")
        self.clips = {"Front": [clip(NOW - 600), clip(NOW - 300)],
                      "Side": []}
        self.coord = _hub(self.clips)

    def _spoken(self):
        return ask(intent_mod.TodayIntent(), self.coord)

    def _digest(self):
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {"test": self.coord}}}
        hass.config_entries = harness._ConfigEntries([self.coord.entry])
        self.services.async_register(hass)
        # The spoken answer takes summarise()'s default window, so the
        # written one is asked for the same day rather than a different one.
        call = type("C", (), {"data": {"config_entry_id": "test",
                                       "hours": 24}})()
        return asyncio.run(hass.services.registered["daily_summary"](call))

    def test_the_written_summary_is_the_sentence_that_is_spoken(self):
        """Both go through summarise(), and this is what that buys: the
        household hears the sentence it would have read, word for word."""
        self.assertTrue(self._spoken().endswith(self._digest()["summary"]),
                        f"{self._spoken()!r} vs {self._digest()['summary']!r}")

    def test_what_is_said_first_is_what_the_digest_lists_as_a_highlight(self):
        """The two present it differently on purpose -- speech leads with it
        because a bare list of totals is the same sentence every day, while a
        digest hands the highlights back as their own list for a caller to
        lay out. They must still be the same fact."""
        spoken, digest = self._spoken(), self._digest()
        self.assertEqual(len(digest["highlights"]), 1)
        highlight = digest["highlights"][0]
        self.assertTrue(spoken.startswith(highlight),
                        f"{spoken!r} does not lead with {highlight!r}")

    def test_a_day_with_nothing_to_single_out_says_only_the_counts(self):
        """Usually empty, and that is the point: a list that always has
        something in it says nothing."""
        self.clips["Side"] = [clip(NOW - 900)]
        digest = self._digest()
        self.assertEqual(digest["highlights"], [])
        self.assertEqual(self._spoken(), digest["summary"])


class TheDigestIsAskedFor(unittest.TestCase):
    """Off unless something calls it. A summary nobody asked for is what
    makes people mute an integration."""

    def test_registering_the_services_schedules_nothing(self):
        helper = sys.modules["homeassistant.helpers.event"]
        scheduled = []
        for name in ("async_track_time_change",
                     "async_track_utc_time_change"):
            original = getattr(helper, name, None)
            setattr(helper, name,
                    lambda *a, _name=name, **k: scheduled.append(_name))
            self.addCleanup(setattr, helper, name, original)
        services = importlib.import_module("tapo_h500.services")
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {}}}
        hass.config_entries = harness._ConfigEntries([])
        services.async_register(hass)
        self.assertEqual(scheduled, [], "nothing is put on a timer")
        self.assertIn("daily_summary", hass.services.registered,
                      "it exists, it just waits to be called")


if __name__ == "__main__":
    unittest.main()
