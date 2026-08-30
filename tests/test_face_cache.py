"""One scan of the window per poll, shared by everything that asks.

Every face sensor, every person sensor and the household count ask the same
question, so one walk of every clip on every camera was happening dozens of
times over one poll's worth of unchanged recordings. Two cameras is a
millisecond and nobody would notice. Sixteen busy ones is a third of a second,
which is a sixth of the poll interval spent answering the same question again.

A cache is only worth having if it cannot go stale, so most of this is about
the three things that change the answer: a new poll, a new name, and the
arrival check's private view of clips that have not been published yet.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

const = importlib.import_module("tapo_h500.const")
NOW = 1_786_600_000
ALICE, BOB = 272465657857, 272465657858


def clip(start, *faces):
    """The hub's own shape: a face id lives on an event_info entry."""
    return {"startTime": start, "endTime": start + 15,
            "events_1": 1 << 5,
            "event_info": [{"face_id": face} for face in faces]}


class TheCache(unittest.TestCase):
    def setUp(self):
        self.coord, _ = harness._build()
        self.coord.cameras = [{"device_id": "cam0", "alias": "Front"}]
        self.coord.data = {"clips": {0: [clip(NOW - 60, ALICE)]}}
        self.scans = 0
        real = self.coord._scan_faces

        def counted(index, source):
            self.scans += 1
            return real(index, source)

        self.coord._scan_faces = counted

    def test_asking_twice_scans_once(self):
        self.coord.faces_seen()
        self.coord.faces_seen()
        self.coord.faces_seen()
        self.assertEqual(self.scans, 1)

    def test_the_answer_is_the_same_answer(self):
        self.assertIs(self.coord.faces_seen(), self.coord.faces_seen())

    def test_each_camera_is_its_own_question(self):
        self.coord.faces_seen()
        self.coord.faces_seen(index=0)
        self.assertEqual(self.scans, 2)
        self.coord.faces_seen(index=0)
        self.assertEqual(self.scans, 2)

    def test_a_new_poll_scans_again(self):
        first = self.coord.faces_seen()
        self.coord.data = {"clips": {0: [clip(NOW - 60, ALICE),
                                          clip(NOW - 30, BOB)]}}
        second = self.coord.faces_seen()
        self.assertEqual(self.scans, 2)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)

    def test_a_poll_that_found_the_same_thing_still_scans_again(self):
        """The check is which dictionary, not what is in it. A poll always
        builds a new one, and comparing contents would be the scan."""
        self.coord.faces_seen()
        self.coord.data = {"clips": {0: [clip(NOW - 60, ALICE)]}}
        self.coord.faces_seen()
        self.assertEqual(self.scans, 2)

    def test_naming_a_face_takes_effect_at_once(self):
        """Naming does not reload the entry -- a reload costs a fresh login
        to a hub that wedges under repeated ones -- so without this the name
        somebody just typed would not appear until the next poll."""
        self.assertIsNone(self.coord.faces_seen()[str(ALICE)]["name"])
        self.coord.entry.options = {
            **self.coord.entry.options, const.CONF_FACE_NAMES: {str(ALICE): "Alice"}}
        self.assertEqual(self.coord.faces_seen()[str(ALICE)]["name"], "Alice")

    def test_renaming_takes_effect_at_once_too(self):
        self.coord.entry.options = {
            **self.coord.entry.options, const.CONF_FACE_NAMES: {str(ALICE): "Alice"}}
        self.coord.faces_seen()
        self.coord.entry.options = {
            **self.coord.entry.options, const.CONF_FACE_NAMES: {str(ALICE): "Alex"}}
        self.assertEqual(self.coord.faces_seen()[str(ALICE)]["name"], "Alex")

    def test_the_arrival_checks_private_view_is_never_cached(self):
        """It runs inside the poll that fetched the recordings, before they
        are published. Caching that answer would hand the published question
        an answer about unpublished data."""
        private = {0: [clip(NOW - 60, ALICE), clip(NOW - 30, BOB)]}
        self.assertEqual(len(self.coord.faces_seen(clips=private)), 2)
        self.assertEqual(len(self.coord.faces_seen()), 1)
        self.assertEqual(len(self.coord.faces_seen(clips=private)), 2)

    def test_the_private_view_does_not_evict_the_published_answer(self):
        """Letting it through the cache would still give right answers and
        would throw the published one away every time it ran, which is once
        per poll -- so the cache would be empty exactly when it is asked."""
        self.coord.faces_seen()
        self.coord.faces_seen(clips={0: [clip(NOW - 30, BOB)]})
        self.coord.faces_seen()
        self.assertEqual(self.scans, 2)


class ThePeopleCache(unittest.TestCase):
    def setUp(self):
        self.coord, _ = harness._build()
        self.coord.cameras = [{"device_id": "cam0", "alias": "Front"}]
        self.coord.data = {"clips": {0: [clip(NOW - 60, ALICE)]}}
        self.coord.entry.options = {
            **self.coord.entry.options, const.CONF_FACE_NAMES: {str(ALICE): "Alice"}}
        self.merges = 0
        real = self.coord._merge_people

        def counted(clips):
            self.merges += 1
            return real(clips)

        self.coord._merge_people = counted

    def test_asking_twice_merges_once(self):
        self.coord.people()
        self.coord.people()
        self.assertEqual(self.merges, 1)

    def test_a_new_poll_merges_again(self):
        self.coord.people()
        self.coord.data = {"clips": {0: [clip(NOW - 60, ALICE)]}}
        self.coord.people()
        self.assertEqual(self.merges, 2)

    def test_a_new_name_merges_again(self):
        self.assertEqual(list(self.coord.people()), ["Alice"])
        self.coord.entry.options = {
            **self.coord.entry.options,
            const.CONF_FACE_NAMES: {str(ALICE): "Alice", str(BOB): "Bob"}}
        self.coord.people()
        self.assertEqual(self.merges, 2)

    def test_the_private_view_is_never_cached(self):
        self.coord.people()
        self.coord.people(clips={0: [clip(NOW - 60, ALICE)]})
        self.coord.people(clips={0: [clip(NOW - 60, ALICE)]})
        self.assertEqual(self.merges, 3)


if __name__ == "__main__":
    unittest.main()
