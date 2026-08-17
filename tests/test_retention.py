"""What retention is not allowed to delete.

One number for everything meant a busy afternoon of motion could evict the
doorbell press that was the whole reason for keeping anything, and it went
silently. Two classes now get their own count -- somebody at the door, and
somebody there at all -- and the important property is which recordings the
protection actually covers: the newest ones, by start time, not whichever the
hub happened to list first.
"""
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
COORDINATOR = (COMPONENT / "coordinator.py").read_text()
CONFIG_FLOW = (COMPONENT / "config_flow.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")
coordinator_mod = importlib.import_module("tapo_h500.coordinator")

NOW = 1_786_600_000


def mask(*codes):
    value = 0
    for code in codes:
        value |= 1 << (code - 1)
    return value


# A real press from this hub that carried no person code: the Side doorbell at
# 9:16 PM on 2026-08-12 logged [2, 10, 17]. Using one that also carries 6 makes
# every press match the person filter too, which hides whether the two counts
# are really independent.
PRESS = mask(2, 10, 17)
PRESS_WITH_PERSON = mask(2, 6, 10, 17)
PERSON = mask(2, 6)
MOTION = mask(2)


def clip(ago, bits=MOTION):
    return {"startTime": NOW - ago, "endTime": NOW - ago + 15, "events_1": bits}


def always(_clip):
    return True


class NewestMatching(unittest.TestCase):
    def test_it_keeps_the_newest(self):
        items = [clip(300), clip(100), clip(200)]
        self.assertEqual(clips.newest_matching(items, always, 2),
                         {NOW - 100, NOW - 200})

    def test_the_hub_order_does_not_decide_which(self):
        """searchVideoWithUTC promises no order. Slicing the list as it
        arrived protected whichever came back first, which on a hub answering
        oldest-first is exactly the ones about to be deleted."""
        oldest_first = [clip(300), clip(200), clip(100)]
        newest_first = [clip(100), clip(200), clip(300)]
        self.assertEqual(clips.newest_matching(oldest_first, always, 1),
                         clips.newest_matching(newest_first, always, 1))

    def test_zero_protects_nothing(self):
        self.assertEqual(clips.newest_matching([clip(100)], always, 0), set())

    def test_a_negative_count_protects_nothing(self):
        """Two recordings, not one: without the guard a negative count slices
        the list from the other end, so [:-1] protects everything except the
        oldest -- and with a single recording that happens to look right."""
        self.assertEqual(
            clips.newest_matching([clip(100), clip(200)], always, -1), set())

    def test_asking_for_more_than_there_are_is_fine(self):
        self.assertEqual(len(clips.newest_matching([clip(100)], always, 50)), 1)

    def test_the_predicate_selects(self):
        items = [clip(100, PRESS), clip(200, MOTION), clip(300, PRESS)]
        kept = clips.newest_matching(
            items, lambda item: clips.has_detection(item, {17}), 5)
        self.assertEqual(kept, {NOW - 100, NOW - 300})

    def test_a_recording_with_no_start_is_skipped(self):
        self.assertEqual(clips.newest_matching([{"events_1": PRESS}],
                                               always, 5), set())


class HasDetection(unittest.TestCase):
    def test_it_finds_a_code_in_the_mask(self):
        self.assertTrue(clips.has_detection(clip(0, PRESS), {17}))

    def test_and_reports_when_it_is_absent(self):
        self.assertFalse(clips.has_detection(clip(0, MOTION), {17}))

    def test_any_of_several_counts(self):
        self.assertTrue(clips.has_detection(clip(0, PERSON), {17, 6}))


class _Client:
    def cameras(self):
        return [{"device_id": "cam0", "alias": "Front"}]

    def recent(self, camera, start, end):
        return []

    def detections(self, camera, start, end):
        return []

    def hub_status(self):
        return {}


def build(**options):
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20, **options), _Client())
    coord.cameras = [{"device_id": "cam0", "alias": "Front"}]
    return coord


class Protected(unittest.TestCase):
    def _with(self, items, **options):
        coord = build(**options)
        coord.data = {"clips": {0: items}}
        return coord._protected(0)

    def test_nothing_is_protected_by_default(self):
        """Both counts default to 0, so an existing installation behaves
        exactly as it did."""
        self.assertEqual(self._with([clip(100, PRESS), clip(200, PERSON)]),
                         set())

    def test_presses_are_protected(self):
        kept = self._with([clip(100, PRESS), clip(200, MOTION)], keep_rings=5)
        self.assertEqual(kept, {NOW - 100})

    def test_people_are_protected(self):
        kept = self._with([clip(100, PERSON), clip(200, MOTION)],
                          keep_person=5)
        self.assertEqual(kept, {NOW - 100})

    def test_motion_is_not(self):
        """A windy afternoon is what fills the disk. Protecting it defeats
        the point."""
        kept = self._with([clip(100, MOTION)], keep_rings=5, keep_person=5)
        self.assertEqual(kept, set())

    def test_the_two_counts_are_separate(self):
        """Ten presses must not use up the allowance for people."""
        items = [clip(ago, PRESS) for ago in range(100, 1100, 100)]
        items += [clip(2000, PERSON)]
        kept = self._with(items, keep_rings=3, keep_person=1)
        self.assertIn(NOW - 2000, kept)
        self.assertEqual(len(kept), 4)

    def test_a_press_with_a_person_counts_for_both(self):
        """One recording landing in two sets, which a set union handles --
        it must not be protected twice or counted twice."""
        kept = self._with([clip(100, PRESS_WITH_PERSON)],
                          keep_rings=5, keep_person=5)
        self.assertEqual(kept, {NOW - 100})

    def test_only_the_newest_survive_the_count(self):
        items = [clip(ago, PERSON) for ago in (100, 200, 300, 400)]
        self.assertEqual(self._with(items, keep_person=2),
                         {NOW - 100, NOW - 200})


class Wiring(unittest.TestCase):
    def test_prune_is_given_the_protected_set(self):
        self.assertIn("async_prune(self.hass, camera, keep,\n"
                      "                                         "
                      "self._protected(index))", COORDINATOR)

    def test_the_option_is_on_the_settings_form(self):
        settings = CONFIG_FLOW.split("async_step_settings", 1)[1]
        self.assertIn("CONF_KEEP_PERSON", settings)

    def test_a_change_to_it_does_not_reload_the_entry(self):
        """The keep counts are read from entry.options at use time -- 
        _protected() and the prune both ask live -- so a reload would rebuild
        the coordinator for a figure it never caches. A reload costs a fresh
        login, and repeated logins are the one thing that wedges an H500."""
        self.assertNotIn(const.CONF_KEEP_PERSON, const.RELOAD_ON_CHANGE)
        self.assertNotIn(const.CONF_KEEP_RINGS, const.RELOAD_ON_CHANGE)
        protected = COORDINATOR.split("def _protected", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("options.get(CONF_KEEP_PERSON", protected,
                      "no longer read live; a reload would be needed again")


if __name__ == "__main__":
    unittest.main()
