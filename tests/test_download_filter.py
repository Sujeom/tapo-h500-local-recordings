"""Which recordings are worth the disk.

off / rings / all was the only choice, and on this firmware two of those three
were the same thing for months: a TD21 doorbell labels every clip video_type
"2", so ring-only matched nothing and downloaded nothing. Even with code 17
identified it is a poor pair of options for a camera facing a road -- the clips
people go back for are the ones with a person in them, and vehicles are the
traffic.

Driven through the real poll, because the filter has to sit in the same place
as the mode check or a "rings only" setting and a type filter would fight.
"""
import importlib
import json
import re
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
FLOW = (COMPONENT / "config_flow.py").read_text()
CONST = (COMPONENT / "const.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000


def mask(*codes):
    value = 0
    for code in codes:
        value |= 1 << (code - 1)
    return value


PERSON = mask(2, 6)
VEHICLE = mask(2, 8)
PRESS = mask(2, 10, 17)


def clip(when, events):
    return {"startTime": when, "endTime": when + 15, "events_1": events}


class _Client:
    def __init__(self):
        self.clips = []

    def cameras(self):
        return [{"device_id": "cam0", "alias": "Front"}]

    def recent(self, camera, start, end):
        return list(self.clips)

    def detections(self, camera, start, end):
        return list(self.clips)

    def hub_status(self):
        return {}


def build(**options):
    client = _Client()
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20, **options), client)
    coord._primed = True
    started = []
    # The real one hands the download to a background task on the entry; here
    # the point is only which clips got that far.
    coord.entry.async_create_background_task = (
        lambda hass, coro, name: (coro.close(), started.append(name))[0])
    return coord, client, started


def offered(coord, client, clips):
    client.clips = clips
    coord._download_new(0, {"device_id": "cam0", "alias": "Front"},
                        list(clips), NOW - const.LOOKBACK_SECONDS)


class Filtering(unittest.TestCase):
    def test_nothing_chosen_downloads_everything(self):
        """What every installation made before this existed has, and keeps."""
        coord, client, started = build()
        offered(coord, client, [clip(NOW - 60, PERSON), clip(NOW - 30, VEHICLE)])
        self.assertEqual(len(started), 2)

    def test_a_chosen_code_keeps_only_matching_recordings(self):
        coord, client, started = build(download_types=["6"])
        offered(coord, client, [clip(NOW - 60, PERSON), clip(NOW - 30, VEHICLE)])
        self.assertEqual(len(started), 1)

    def test_several_codes_are_an_either_or(self):
        coord, client, started = build(download_types=["6", "17"])
        offered(coord, client, [clip(NOW - 90, PERSON), clip(NOW - 60, PRESS),
                                clip(NOW - 30, VEHICLE)])
        self.assertEqual(len(started), 2)

    def test_a_code_that_fired_alongside_others_still_matches(self):
        """detection_types lists everything that fired at once. A person who
        also set off plain motion is still a person."""
        coord, client, started = build(download_types=["6"])
        offered(coord, client, [clip(NOW - 60, mask(2, 6, 8, 22))])
        self.assertEqual(len(started), 1)

    def test_downloading_off_still_wins(self):
        """The filter narrows what is downloaded; it must not start
        downloading on a hub where that was turned off."""
        coord, client, started = build(auto_download="off",
                                       download_types=["6"])
        offered(coord, client, [clip(NOW - 60, PERSON)])
        self.assertEqual(started, [])

    def test_rings_only_and_a_filter_both_apply(self):
        """Two narrowing settings must both narrow, not one override the
        other."""
        coord, client, started = build(auto_download="rings",
                                       download_types=["6"])
        offered(coord, client, [clip(NOW - 90, PERSON),   # person, not a press
                                clip(NOW - 60, PRESS)])   # a press, no person
        self.assertEqual(started, [])

    def test_a_recording_with_no_detection_at_all_is_filtered_out(self):
        coord, client, started = build(download_types=["6"])
        offered(coord, client, [{"startTime": NOW - 60, "endTime": NOW - 45}])
        self.assertEqual(started, [])


class Parsing(unittest.TestCase):
    def test_codes_arrive_as_strings_from_the_form(self):
        coord, _, _ = build(download_types=["6", "17"])
        self.assertEqual(coord.download_types, {6, 17})

    def test_nonsense_is_skipped_rather_than_fatal(self):
        """This is stored options: a hand-edited entry should cost one code,
        not every poll."""
        coord, _, _ = build(download_types=["6", "person", None])
        self.assertEqual(coord.download_types, {6})

    def test_unset_is_an_empty_set_not_every_code(self):
        coord, _, _ = build()
        self.assertEqual(coord.download_types, set())


class Form(unittest.TestCase):
    def test_every_code_the_hub_can_report_is_offered(self):
        """Read from DETECTION_NAMES rather than listed here, or a code named
        later would be missing from the form with nothing to say so.

        Scoped to the settings step: splitting on the constant matched its
        import line, where the assertion could never fail.
        """
        step = FLOW.split("async def async_step_settings", 1)[1]
        body = step.split("CONF_DOWNLOAD_TYPES,", 1)[1].split("})", 1)[0]
        self.assertIn("sorted(DETECTION_NAMES)", body)

    def test_every_code_has_a_label(self):
        """Without one the box lists bare numbers, and 22 means nothing."""
        codes = set(re.findall(r'^\s+(\d+): "', CONST, re.M))
        offered_labels = set(STRINGS["selector"]["download_types"]["options"])
        self.assertEqual(codes - offered_labels, set())

    def test_it_can_be_left_empty(self):
        """A required multi-select with an empty default is a form that cannot
        be submitted, and empty is the setting most people want."""
        self.assertIn("vol.Optional(\n                CONF_DOWNLOAD_TYPES", FLOW)

    def test_changing_it_does_not_reload_the_entry(self):
        """A reload costs a fresh login, and repeated logins are the one thing
        that wedges an H500. Nothing about the connection changes here."""
        self.assertNotIn("CONF_DOWNLOAD_TYPES", CONST.split(
            "RELOAD_ON_CHANGE = (", 1)[1].split(")", 1)[0])


if __name__ == "__main__":
    unittest.main()
