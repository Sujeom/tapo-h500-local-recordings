"""The hub's field names live in one place and are checked against real data.

The protocol is undocumented and everything arrives as dictionaries, so a key
that has been renamed -- or was never spelled the way the code guesses --
fails by returning None. Silently, forever, in a green suite. That is exactly
how the storage warning stayed dead for months: the reading was looked up as
`storage_total` where the parser produced `storage_total_gb`, and nothing
anywhere said so.

There is no type checker here, and a dataclass wrapper would only move the
guess into its constructor. What catches it is one list of the names, every
accessor built from that list, and real captured hub responses read back
through those accessors -- so a rename fails on recorded data rather than on
somebody's doorbell.
"""
import importlib
import re
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")

# Captured off this hub. A detection from the log, and the clip the index
# returned for the same event -- the two spell their times differently, which
# is the whole reason the accessors take a list of names.
REAL_DETECTION = {
    "alarm_type": 20, "events_1": 524450, "start_time": 1786542131,
    "event_info": [{"face_bitmap": 0, "face_id": 272465657857}],
}
REAL_CLIP = {
    "startTime": 1786542131, "endTime": 1786542147, "video_type": "2",
}


class TheAccessorsReadRealData(unittest.TestCase):
    """Every one of them, against what the hub actually sent."""

    def test_a_clips_own_times(self):
        self.assertEqual(clips.start_of(REAL_CLIP), 1786542131)
        self.assertEqual(clips.end_of(REAL_CLIP), 1786542147)

    def test_a_detections_times_are_spelled_differently(self):
        self.assertEqual(clips.start_of(REAL_DETECTION), 1786542131)
        self.assertIsNone(clips.end_of(REAL_DETECTION),
                          "the detection log carries no end")

    def test_what_fired(self):
        self.assertEqual(clips.detection_types(REAL_DETECTION), [2, 6, 8, 20])
        self.assertEqual(clips.describe_detection(REAL_DETECTION),
                         "motion + person + vehicle + face")

    def test_the_faces_it_recognised(self):
        self.assertEqual(clips.face_ids(REAL_DETECTION), [272465657857])

    def test_the_hubs_own_label(self):
        self.assertEqual(clips.hub_label(REAL_CLIP), "2")

    def test_an_entry_carrying_none_of_it_answers_nothing_rather_than_raising(self):
        for accessor in (clips.start_of, clips.end_of, clips.hub_label):
            with self.subTest(accessor.__name__):
                self.assertIsNone(accessor({}))
        self.assertEqual(clips.face_ids({}), [])
        self.assertEqual(clips.detection_types({}), [])


class TheNamesAreInOnePlace(unittest.TestCase):
    SOURCE = (COMPONENT / "clips.py").read_text()

    def test_every_accessor_reads_the_list_rather_than_a_literal(self):
        """A literal in an accessor is a name that can drift away from the
        one beside it without anything noticing."""
        for spelling in ('"startTime"', '"endTime"', '"start_time"',
                         '"end_time"', '"alarm_type"', '"events_1"',
                         '"event_info"', '"face_id"'):
            with self.subTest(spelling):
                # Once, in HUB_FIELDS, and nowhere else in this module.
                self.assertEqual(self.SOURCE.count(spelling), 1, spelling)

    def test_the_list_covers_what_the_accessors_ask_for(self):
        for key in ("start", "end", "alarm", "mask", "faces", "face", "label"):
            with self.subTest(key):
                self.assertIn(key, clips.HUB_FIELDS)
                self.assertTrue(clips.HUB_FIELDS[key])

    def test_the_old_name_still_works_beside_the_new_one(self):
        """Four spellings of the label across firmwares and lookups, tried in
        order. Dropping one silently reclassifies every clip that used it, so
        the four are named here rather than read off the list under test."""
        self.assertEqual(clips.TYPE_FIELDS, clips.HUB_FIELDS["label"])
        self.assertEqual(
            set(clips.TYPE_FIELDS),
            {"video_type", "detection_type", "event_type", "type"})
        for spelling in ("video_type", "detection_type", "event_type", "type"):
            with self.subTest(spelling):
                self.assertEqual(clips.hub_label({spelling: "x"}), "x")

    def test_the_times_are_read_under_both_spellings(self):
        """Named here too. The clip index and the detection log disagree, and
        a list that lost one would answer None for half of them."""
        self.assertEqual(set(clips.HUB_FIELDS["start"]),
                         {"startTime", "start_time"})
        self.assertEqual(set(clips.HUB_FIELDS["end"]),
                         {"endTime", "end_time"})

    def test_a_clip_gets_everything_the_detection_knew(self):
        """The copy list is built from the same names the accessors read. One
        the accessors read and the copy misses is a clip that answers None to
        a question the detection could have answered."""
        clip = {"startTime": 1786542131, "endTime": 1786542147}
        clips.attach_detections([clip], [REAL_DETECTION])
        self.assertEqual(clips.detection_types(clip), [2, 6, 8, 20])
        self.assertEqual(clips.face_ids(clip), [272465657857])
        self.assertEqual(clip["alarm_type"], 20)

    def test_the_first_spelling_present_wins(self):
        self.assertEqual(clips.start_of({"startTime": 1, "start_time": 2}), 1)


class NobodyElseReadsThemRaw(unittest.TestCase):
    """Every other module asks an accessor what a clip says.

    Reading, specifically. These same words are the integration's own output
    field names on events and service schemas, and writing one is not the
    thing that goes wrong -- a name this code chose is a name this code
    controls. What goes wrong is reading a name the hub might have changed.

    Two modules do read one, deliberately, and they are named here so that a
    third has to be argued for rather than added.
    """

    ALLOWED = {
        # Both hand the hub's own code back untouched, beside the decoded
        # one, for an automation matching on something this code has not
        # named yet.
        "event.py": {"alarm_type"},
        # ...and the service call's own schema fields, which happen to be
        # spelled the same way. A name this code chose is a name it controls.
        "services.py": {"alarm_type", "start_time", "end_time"},
        # The sidecar written beside a downloaded clip and read back later,
        # also this code's own spelling.
        "media.py": {"start_time"},
    }
    # Only the names that mean nothing else. "event_type", "type" and
    # "face_id" are all words this integration and Home Assistant use for
    # their own things, so sweeping for them finds honest code and teaches
    # people to add allowances -- which is how a rule like this stops meaning
    # anything.
    NAMES = {"startTime", "endTime", "start_time", "end_time",
             "alarm_type", "events_1", "event_info"}

    @staticmethod
    def _reads(source: str, name: str) -> bool:
        """A lookup, not a key being written."""
        return bool(re.search(
            rf'\.get\(\s*["\']{name}["\']|\[\s*["\']{name}["\']\s*\]', source))

    def test_no_other_module_reads_a_hub_field(self):
        offenders = {}
        for path in sorted(COMPONENT.glob("*.py")):
            if path.name == "clips.py":
                continue
            source = path.read_text()
            found = {name for name in self.NAMES
                     if self._reads(source, name)}
            unexpected = found - self.ALLOWED.get(path.name, set())
            if unexpected:
                offenders[path.name] = sorted(unexpected)
        self.assertEqual(offenders, {},
                         "read it through an accessor in clips.py, or add it "
                         "here with the reason it has to be raw")

    def test_the_allowances_are_all_still_used(self):
        """An allowance nobody needs is a hole nobody closed."""
        stale = {}
        for name, allowed in self.ALLOWED.items():
            source = (COMPONENT / name).read_text()
            unused = {field for field in allowed
                      if not self._reads(source, field)}
            if unused:
                stale[name] = sorted(unused)
        self.assertEqual(stale, {})

    def test_the_sweep_would_catch_a_new_one(self):
        self.assertTrue(self._reads('x = clip.get("startTime")', "startTime"))
        self.assertTrue(self._reads('x = clip["events_1"]', "events_1"))
        self.assertFalse(self._reads('return {"start_time": start}',
                                     "start_time"),
                         "writing our own field name is not the problem")


if __name__ == "__main__":
    unittest.main()
