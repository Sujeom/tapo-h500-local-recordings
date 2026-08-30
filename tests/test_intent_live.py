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


if __name__ == "__main__":
    unittest.main()
