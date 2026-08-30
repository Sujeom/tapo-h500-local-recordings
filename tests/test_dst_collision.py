"""Two recordings in the repeated hour must not become one file.

When daylight saving ends, the local clock runs 01:00-01:59 twice. Downloads
are filed as <camera>/<YYYY-MM-DD>/<HHMMSS>.mp4 in LOCAL time, so both passes
produce the same name and the second overwrites the first -- silently, once a
year, and only ever discovered by going to look for footage that is no longer
there.

The shared harness pins local time to a fixed -07:00 with no daylight saving,
which is right for every other test and cannot show this one. These use a real
zone for the transition they are about.
"""
import importlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

media = importlib.import_module("tapo_h500.media")
dt_util = sys.modules["homeassistant.util.dt"]

NEW_YORK = ZoneInfo("America/New_York")
# 2026-11-01, when the US clock falls back. Both are 01:00 local.
FIRST_PASS = 1_793_509_200      # 05:00 UTC, still EDT
SECOND_PASS = 1_793_512_800     # 06:00 UTC, now EST

CAMERA = {"device_id": "cam0", "alias": "Front"}


class _Hass:
    def __init__(self, root):
        self.config = type("C", (), {"media_dirs": {"local": str(root)}})()


class TheRepeatedHour(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / "_dst_tmp"
        self.root.mkdir(exist_ok=True)
        self.hass = _Hass(self.root)
        self._local = dt_util.LOCAL
        dt_util.LOCAL = NEW_YORK
        self.addCleanup(setattr, dt_util, "LOCAL", self._local)
        self.addCleanup(self._clean)

    def _clean(self):
        for path in sorted(self.root.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        self.root.rmdir()

    def test_the_two_passes_really_are_the_same_wall_clock(self):
        """The premise. Without this the rest could pass for the wrong reason."""
        first = datetime.fromtimestamp(FIRST_PASS, NEW_YORK)
        second = datetime.fromtimestamp(SECOND_PASS, NEW_YORK)
        self.assertEqual(first.strftime("%Y-%m-%d %H:%M"),
                         second.strftime("%Y-%m-%d %H:%M"))
        self.assertNotEqual(first.utcoffset(), second.utcoffset())
        self.assertEqual((first.fold, second.fold), (0, 1))

    def test_they_do_not_share_a_filename(self):
        """The fix. An hour apart in real time is two recordings, and two
        recordings are two files."""
        first = media.clip_path(self.hass, CAMERA, FIRST_PASS, ".mp4")
        second = media.clip_path(self.hass, CAMERA, SECOND_PASS, ".mp4")
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, second.parent, "same local day")

    def test_the_earlier_one_keeps_the_name_it_always_had(self):
        """Nothing already on disk may be orphaned by this. Only the second
        pass -- the one that was being lost anyway -- gets a new name."""
        first = media.clip_path(self.hass, CAMERA, FIRST_PASS, ".mp4")
        self.assertEqual(first.name, "010000.mp4")

    def test_the_newest_still_sorts_last(self):
        """_newest_thumbnail takes the last name in sorted order, so the later
        recording has to sort after the earlier one."""
        names = sorted([media.clip_path(self.hass, CAMERA, FIRST_PASS, ".jpg").name,
                        media.clip_path(self.hass, CAMERA, SECOND_PASS, ".jpg").name])
        self.assertEqual(names[-1],
                         media.clip_path(self.hass, CAMERA,
                                         SECOND_PASS, ".jpg").name)

    def test_each_name_reads_back_as_its_own_instant(self):
        """`already downloaded` is answered by reading the time out of the
        filename, so a name that reads back as the OTHER pass would answer for
        the wrong recording -- and skip downloading one of them."""
        for moment in (FIRST_PASS, SECOND_PASS):
            with self.subTest(moment=moment):
                path = media.clip_path(self.hass, CAMERA, moment, ".mp4")
                self.assertEqual(media._start_from_path(path), moment)

    def test_an_ordinary_hour_is_untouched(self):
        """Every recording outside that one hour keeps the plain name, or this
        fix orphans the whole archive to solve one hour a year."""
        ordinary = int(datetime(2026, 6, 1, 15, 30,
                                tzinfo=timezone.utc).timestamp())
        path = media.clip_path(self.hass, CAMERA, ordinary, ".mp4")
        self.assertTrue(path.stem.isdigit(), path.name)
        self.assertEqual(media._start_from_path(path), ordinary)


if __name__ == "__main__":
    unittest.main()
