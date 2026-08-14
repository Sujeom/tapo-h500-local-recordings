"""Somebody handling the camera itself.

Code 19 is the one detection that must not be allowed to scroll past.
Everything else this integration reports happened outside the house; this is
the camera being knocked, covered or lifted off its mount -- confirmed on real
hardware by doing exactly that at 11:16:16 on 2026-08-13 -- and if it is real
then the recordings that follow it are the ones that will be missing.

It already had a binary sensor, which holds for thirty seconds and then clears.
That is right for a history graph and useless for a fact somebody needs to see
whenever they next open Home Assistant.

The finder is run for real against the coordinator; the issue is checked
statically, the way the other five are.
"""
import asyncio
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
REPAIRS = (COMPONENT / "repairs.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
DAY = const.LOOKBACK_SECONDS

CAMERAS = [{"device_id": "cam0", "alias": "Front"},
           {"device_id": "cam1", "alias": "Side"}]


def mask(*codes):
    value = 0
    for code in codes:
        value |= 1 << (code - 1)
    return value


TAMPER = mask(6, 19, 20)
PERSON = mask(2, 6)


def clip(when, events):
    return {"startTime": when, "endTime": when + 15, "events_1": events}


class _Client:
    def __init__(self):
        self.per_camera = {}

    def cameras(self):
        return list(CAMERAS)

    def _for(self, camera):
        return list(self.per_camera.get(camera["alias"], []))

    def recent(self, camera, start, end):
        return self._for(camera)

    def detections(self, camera, start, end):
        return self._for(camera)

    def hub_status(self):
        return {}


def build(per_camera=None):
    client = _Client()
    client.per_camera = per_camera or {}
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20), client)
    coord._download_new = lambda *a, **k: None
    coord.data = asyncio.run(coord._async_update_data())
    return coord


class Finding(unittest.TestCase):
    def test_a_tamper_detection_is_found(self):
        coord = build({"Front": [clip(NOW - 600, TAMPER)]})
        self.assertEqual(coord.tampered(DAY), [("Front", NOW - 600)])

    def test_an_ordinary_recording_is_not_one(self):
        coord = build({"Front": [clip(NOW - 600, PERSON)]})
        self.assertEqual(coord.tampered(DAY), [])

    def test_it_is_code_19_and_not_the_codes_beside_it(self):
        """The real event carried 6 and 20 as well -- somebody was standing
        there doing it, and the hub recognised them -- so a test using only
        that recording cannot tell which of the three is being matched.

        19 is one of only two codes ever seen alone, which is what makes this
        separable at all.
        """
        alone = build({"Front": [clip(NOW - 600, mask(19))]})
        self.assertEqual(len(alone.tampered(DAY)), 1)
        recognised = build({"Front": [clip(NOW - 600, mask(2, 6, 20))]})
        self.assertEqual(recognised.tampered(DAY), [])

    def test_it_says_which_camera(self):
        """"A camera was interfered with" is not actionable when there are
        two of them."""
        coord = build({"Front": [clip(NOW - 600, PERSON)],
                       "Side": [clip(NOW - 300, TAMPER)]})
        self.assertEqual([name for name, _ in coord.tampered(DAY)], ["Side"])

    def test_the_newest_comes_first(self):
        """The issue names one, and it has to be the most recent."""
        coord = build({"Front": [clip(NOW - 6000, TAMPER),
                                 clip(NOW - 300, TAMPER)]})
        self.assertEqual([when for _, when in coord.tampered(DAY)],
                         [NOW - 300, NOW - 6000])

    def test_all_of_them_are_counted(self):
        """Once is a knock; repeatedly is not, and the difference is the whole
        reason the count is in the message."""
        coord = build({"Front": [clip(NOW - 6000, TAMPER),
                                 clip(NOW - 300, TAMPER)],
                       "Side": [clip(NOW - 400, TAMPER)]})
        self.assertEqual(len(coord.tampered(DAY)), 3)

    def test_anything_older_than_the_window_is_gone(self):
        """Which is how the issue clears itself: nothing here can dismiss it,
        so it has to age out."""
        coord = build({"Front": [clip(NOW - 600, TAMPER)]})
        self.assertEqual(coord.tampered(300), [])
        self.assertEqual(len(coord.tampered(DAY)), 1)

    def test_a_recording_with_no_start_is_ignored(self):
        coord = build({"Front": [{"endTime": NOW, "events_1": TAMPER}]})
        self.assertEqual(coord.tampered(DAY), [])

    def test_it_reads_the_mask_not_the_headline_code(self):
        """alarm_type reports only the most significant code, and 20 outranks
        19 -- so a camera lifted off its mount while somebody the hub
        recognised stood there would report as a face and nothing else."""
        coord = build({"Front": [{"startTime": NOW - 60, "endTime": NOW - 45,
                                  "events_1": TAMPER, "alarm_type": 20}]})
        self.assertEqual(len(coord.tampered(DAY)), 1)


class Issue(unittest.TestCase):
    def test_it_is_checked_every_poll(self):
        self.assertIn("_tampered(hass, entry_id, coordinator)", REPAIRS)

    def test_it_clears_itself(self):
        """An issue that never clears is worse than one that never appears."""
        body = REPAIRS.split("def _tampered", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("async_delete_issue", body)
        self.assertIn("if not events:", body)

    def test_it_is_an_error_not_a_warning(self):
        """Every other issue here is about footage being lost. This one is
        about somebody standing at the camera."""
        body = REPAIRS.split("def _tampered", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("IssueSeverity.ERROR", body)

    def test_it_names_the_camera_and_the_time(self):
        """"Someone touched your camera at some point" is not usable."""
        body = REPAIRS.split("def _tampered", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"camera": camera', body)
        self.assertIn('"when":', body)
        self.assertIn('"count":', body)

    def test_the_time_is_local(self):
        """A tamper report in UTC sends somebody looking at the wrong hour of
        footage."""
        body = REPAIRS.split("def _tampered", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("dt_util.as_local(", body)

    def test_it_names_the_newest_report(self):
        body = REPAIRS.split("def _tampered", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("events[0]", body)

    def test_it_has_a_title_and_a_description(self):
        issue = STRINGS["issues"]["camera_tampered"]
        self.assertIn("title", issue)
        for placeholder in ("{camera}", "{when}", "{count}"):
            self.assertIn(placeholder, issue["description"])

    def test_the_description_says_it_can_be_a_knock(self):
        """Overstating this is how a real alarm gets muted."""
        text = STRINGS["issues"]["camera_tampered"]["description"].lower()
        self.assertIn("knock", text)


if __name__ == "__main__":
    unittest.main()
