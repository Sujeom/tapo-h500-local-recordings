"""Unusual-activity detection, exercised directly.

These are pure functions over a list of clips, so unlike most of this suite
they can be run for real rather than inspected. The behaviour worth pinning is
what happens at the two ends where a ratio breaks: a camera that normally sees
nothing, where any event is infinitely above typical, and a camera that is
always busy, which must not sit permanently alarmed.
"""
import importlib
import sys
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
package = types.ModuleType("tapo_h500")
package.__path__ = [str(COMPONENT)]
sys.modules.setdefault("tapo_h500", package)
clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
DAY = 86400


def at(*offsets):
    """Clips this many seconds before now."""
    return [{"startTime": NOW - offset} for offset in offsets]


def busy(clips_list):
    return clips.unusually_busy(
        clips_list, NOW, DAY, const.UNUSUAL_MULTIPLIER, const.UNUSUAL_FLOOR)


class Counting(unittest.TestCase):
    def test_only_clips_inside_the_period_count(self):
        self.assertEqual(clips.events_since(at(60, 600, 7200), NOW - 3600), 2)

    def test_a_clip_with_no_timestamp_does_not_count(self):
        self.assertEqual(clips.events_since([{}], NOW - 3600), 0)

    def test_the_baseline_is_per_hour_not_per_window(self):
        # 48 events across a 24h window is two an hour.
        self.assertAlmostEqual(
            clips.hourly_baseline(at(*range(0, 48 * 1800, 1800)), NOW, DAY), 2.0)

    def test_an_empty_window_has_a_zero_baseline_not_a_crash(self):
        self.assertEqual(clips.hourly_baseline([], NOW, DAY), 0.0)


class Flagging(unittest.TestCase):
    def test_a_quiet_camera_is_not_alarmed_by_one_delivery(self):
        """Its baseline is zero, where any event is infinitely above typical.
        Without the floor this would be the most common false alarm."""
        self.assertFalse(busy(at(60)))
        self.assertFalse(busy(at(60, 120, 180)))

    def test_a_burst_on_a_quiet_camera_is_flagged(self):
        self.assertTrue(busy(at(60, 120, 180, 240, 300)))

    def test_a_permanently_busy_camera_does_not_sit_alarmed(self):
        """Two an hour all day, and two in the last hour: entirely typical."""
        steady = at(*range(0, DAY, 1800))
        self.assertFalse(busy(steady))

    def test_a_genuinely_busy_camera_is_judged_against_its_own_rate(self):
        """The case the multiplier exists for, and the one the earlier tests
        missed: a main-road doorbell at ten an hour, with ten in the last hour.
        That is above the floor, so only comparing against its own baseline
        keeps it quiet. Judged on the floor alone it would alarm permanently.
        """
        steady = at(*range(0, DAY, 360))          # 10/hour, all day
        self.assertGreaterEqual(clips.events_since(steady, NOW - 3600),
                                const.UNUSUAL_FLOOR)
        self.assertFalse(busy(steady))

    def test_a_busy_camera_is_still_flagged_by_a_real_spike(self):
        steady = at(*range(3600, DAY, 1800))       # 2/hour, none this hour
        spike = at(*range(0, 3600, 120))           # 30 in the last hour
        self.assertTrue(busy(steady + spike))

    def test_the_floor_is_absolute(self):
        """Below it nothing is flagged however far above baseline it is."""
        below = const.UNUSUAL_FLOOR - 1
        self.assertFalse(busy(at(*range(0, below * 60, 60))))

    def test_nothing_at_all_is_not_unusual(self):
        self.assertFalse(busy([]))


class Thresholds(unittest.TestCase):
    def test_the_floor_is_high_enough_to_be_useful(self):
        """1 or 2 would flag an ordinary visitor on a quiet door."""
        self.assertGreaterEqual(const.UNUSUAL_FLOOR, 3)

    def test_the_multiplier_actually_multiplies(self):
        self.assertGreater(const.UNUSUAL_MULTIPLIER, 1)


class NightWindow(unittest.TestCase):
    """A window that wraps midnight, and the one signal built on it."""

    def test_an_hour_inside_a_wrapping_window(self):
        self.assertTrue(clips.in_night(23, 22, 6))
        self.assertTrue(clips.in_night(2, 22, 6))
        self.assertTrue(clips.in_night(22, 22, 6))

    def test_an_hour_outside_it(self):
        self.assertFalse(clips.in_night(12, 22, 6))
        self.assertFalse(clips.in_night(6, 22, 6))
        self.assertFalse(clips.in_night(21, 22, 6))

    def test_a_window_that_does_not_wrap_still_works(self):
        self.assertTrue(clips.in_night(2, 1, 5))
        self.assertFalse(clips.in_night(6, 1, 5))

    def test_an_empty_window_is_never_night(self):
        """Start equal to end is how someone turns this off; read naively it
        would mark either nothing or everything."""
        self.assertFalse(clips.in_night(3, 0, 0))
        self.assertFalse(clips.in_night(3, 22, 22))

    def test_tampering_is_notable_at_any_hour(self):
        """Somebody handling the camera outranks somebody at the door. The
        recordings after a real tamper are the ones that will be missing,
        so its alert must sound different at noon as much as at night."""
        tamper = {"events_1": 1 << 18}
        self.assertTrue(clips.notable(tamper, 14, 22, 6))
        self.assertTrue(clips.notable(tamper, 2, 22, 6))

    def test_only_an_unfamiliar_face_at_night_is_notable(self):
        unknown = {"events_1": (1 << 1) | (1 << 5) | (1 << 21)}   # 2, 6, 22
        self.assertTrue(clips.notable(unknown, 2, 22, 6))
        self.assertFalse(clips.notable(unknown, 14, 22, 6))

    def test_motion_at_night_is_a_cat(self):
        self.assertFalse(clips.notable({"events_1": 1 << 1}, 2, 22, 6))

    def test_a_recognised_face_at_night_is_someone_coming_home(self):
        known = {"events_1": (1 << 5) | (1 << 19)}                # 6, 20
        self.assertFalse(clips.notable(known, 2, 22, 6))


if __name__ == "__main__":
    unittest.main()
