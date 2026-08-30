"""Reading one picture must not read the whole archive.

Names sort chronologically -- <date>/<HHMMSS>.jpg -- so the newest thumbnail
is the last file in the newest day that has one. It was found by globbing
every path under the camera and sorting the lot: a year of a busy doorbell is
twenty thousand paths, walked on every frontend look at the camera picture,
which is several times a second while somebody is watching.
"""
import shutil
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import ha_stubs  # noqa: E402

newest_thumbnail = ha_stubs._real_media_attr("_newest_thumbnail")


class TheNewestThumbnail(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / "_thumb_tmp"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _write(self, day, name, body=b"x"):
        folder = self.root / day
        folder.mkdir(exist_ok=True)
        (folder / name).write_bytes(body)

    def test_the_last_file_of_the_last_day(self):
        self._write("2026-08-28", "235959.jpg", b"yesterday")
        self._write("2026-08-29", "080000.jpg", b"morning")
        self._write("2026-08-29", "213000.jpg", b"evening")
        self.assertEqual(newest_thumbnail(self.root), b"evening")

    def test_a_newer_day_beats_a_later_time_on_an_older_one(self):
        self._write("2026-08-28", "235959.jpg", b"late yesterday")
        self._write("2026-08-29", "000100.jpg", b"early today")
        self.assertEqual(newest_thumbnail(self.root), b"early today")

    def test_a_day_holding_no_thumbnail_is_skipped(self):
        """Its clips' thumbnails may all have failed to render, or retention
        may have emptied it and left the folder behind."""
        self._write("2026-08-28", "120000.jpg", b"the real newest")
        (self.root / "2026-08-29").mkdir()
        self.assertEqual(newest_thumbnail(self.root), b"the real newest")

    def test_a_day_holding_only_videos_is_skipped(self):
        self._write("2026-08-28", "120000.jpg", b"the real newest")
        self._write("2026-08-29", "120000.mp4", b"video")
        self.assertEqual(newest_thumbnail(self.root), b"the real newest")

    def test_a_camera_that_has_recorded_nothing_gives_nothing(self):
        self.assertIsNone(newest_thumbnail(self.root))

    def test_a_camera_with_no_folder_at_all_is_not_an_error(self):
        """Before the first download there is no directory. Listing one that
        does not exist raises where globbing it does not."""
        self.assertIsNone(newest_thumbnail(self.root / "never-recorded"))

    def test_a_stray_file_beside_the_days_is_not_mistaken_for_one(self):
        (self.root / "notes.txt").write_bytes(b"x")
        self._write("2026-08-28", "120000.jpg", b"the real newest")
        self.assertEqual(newest_thumbnail(self.root), b"the real newest")

    def test_the_repeated_hour_reads_in_the_right_order(self):
        """The clock going back writes 010000.jpg and then 010000b.jpg for
        the second pass through that hour, and the second one is later."""
        self._write("2026-11-01", "010000.jpg", b"first pass")
        self._write("2026-11-01", "010000b.jpg", b"second pass")
        self.assertEqual(newest_thumbnail(self.root), b"second pass")

    def test_it_does_not_read_the_whole_archive(self):
        """A year of a busy doorbell, read twenty times. Globbing the lot
        took 1.3 seconds; the bound is a fortieth of that and thirty times
        what this costs."""
        for day in range(200):
            folder = self.root / f"2025-{day // 31 + 1:02d}-{day % 31 + 1:02d}"
            folder.mkdir(exist_ok=True)
            for minute in range(60):
                (folder / f"00{minute:02d}00.jpg").write_bytes(b"x")
        started = time.perf_counter()
        for _ in range(20):
            newest_thumbnail(self.root)
        self.assertLess(time.perf_counter() - started, 0.3)


if __name__ == "__main__":
    unittest.main()
