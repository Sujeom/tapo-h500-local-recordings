"""Preview frames are the one thing on disk nothing ever deleted.

A preview is written at the path the clip's own thumbnail would use, so a
later download finds it already there and retention removes it alongside the
video. That is the happy path. The point of a preview, though, is the clip
that is *not* downloaded -- rings-only mode, a download-type filter, automatic
downloads off entirely -- and there the frame is all there is. Retention walks
videos and these have no video, so one file per event stayed for the life of
the installation.

Not a retention setting: that number defaults to keeping everything and is
about recordings. A cache of single frames gets a ceiling instead.
"""
import asyncio
import importlib
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import ha_stubs  # noqa: E402

const = importlib.import_module("tapo_h500.const")
# Straight from the real module, never through `tapo_h500.media`. That name is
# the stub, which blanks every coroutine that touches a hub or a disk -- and
# the contact-sheet tests replace `camera_dir` on it for the whole run, so
# reading a path through it would give this file somebody else's answer.
async_prune_previews = ha_stubs._real_media_attr("async_prune_previews")
camera_dir = ha_stubs._real_media_attr("camera_dir")

CAMERA = {"device_id": "cam0", "alias": "Front"}
DAY = "2026-08-29"


class _Hass:
    def __init__(self, root):
        self.config = type("C", (), {"media_dirs": {"local": str(root)}})()

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class TheCeiling(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / "_preview_tmp"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.hass = _Hass(self.root)
        self.day = camera_dir(self.hass, CAMERA) / DAY
        self.day.mkdir(parents=True)

    def _make(self, count, suffix=".jpg", first=0):
        """`count` files a second apart, oldest first."""
        made = []
        for offset in range(first, first + count):
            name = f"{offset // 3600:02d}{offset // 60 % 60:02d}{offset % 60:02d}"
            path = self.day / f"{name}{suffix}"
            path.write_bytes(b"x")
            made.append(path)
        return made

    def _sweep(self):
        return asyncio.run(async_prune_previews(self.hass, CAMERA))

    def _left(self, suffix=".jpg"):
        return sorted(p.name for p in self.day.glob(f"*{suffix}"))

    def test_under_the_ceiling_nothing_goes(self):
        self._make(const.PREVIEW_KEEP)
        self.assertEqual(self._sweep(), [])
        self.assertEqual(len(self._left()), const.PREVIEW_KEEP)

    def test_over_it_the_oldest_go(self):
        made = self._make(const.PREVIEW_KEEP + 5)
        removed = self._sweep()
        self.assertEqual(len(removed), 5)
        self.assertEqual(sorted(Path(p).name for p in removed),
                         sorted(p.name for p in made[:5]))
        self.assertEqual(len(self._left()), const.PREVIEW_KEEP)

    def test_the_newest_always_survives(self):
        """It is the frame the camera entity serves. Evicting it would blank
        the picture, which is the failure this whole mechanism exists to
        prevent."""
        made = self._make(const.PREVIEW_KEEP * 2)
        self._sweep()
        self.assertTrue(made[-1].exists())

    def test_a_thumbnail_with_its_clip_is_never_a_stray(self):
        """Those belong to retention, which deletes them with the video.
        Sweeping them here would take the picture off a clip still on disk."""
        kept = self._make(const.PREVIEW_KEEP + 5)
        for path in kept[:5]:
            path.with_suffix(".mp4").write_bytes(b"video")
        self.assertEqual(self._sweep(), [])
        self.assertEqual(len(self._left()), const.PREVIEW_KEEP + 5)

    def test_a_ts_clip_counts_as_its_clip_too(self):
        """Conversion to MP4 is optional; unconverted downloads stay .ts."""
        kept = self._make(const.PREVIEW_KEEP + 5)
        for path in kept[:5]:
            path.with_suffix(".ts").write_bytes(b"video")
        self.assertEqual(self._sweep(), [])

    def test_the_paths_are_the_ones_the_media_browser_uses(self):
        self._make(const.PREVIEW_KEEP + 1)
        removed = self._sweep()
        self.assertEqual(len(removed), 1)
        self.assertFalse(removed[0].startswith("/"), removed[0])
        self.assertTrue((self.root / removed[0]).parent.is_dir())

    def test_a_camera_with_no_directory_is_not_an_error(self):
        """Nothing has been recorded yet. Every poll would otherwise raise."""
        self.assertEqual(
            asyncio.run(async_prune_previews(
                self.hass, {"device_id": "cam9", "alias": "Nowhere"})),
            [])


class Wiring(unittest.TestCase):
    COORDINATOR = (Path(__file__).parents[1] / "custom_components" /
                   "tapo_h500" / "coordinator.py").read_text()

    def test_the_sweep_runs_where_previews_are_made(self):
        """On a timer it would wake a camera nobody looks at; here one arrives
        and one is swept."""
        self.assertIn("await async_preview_clip(self.hass, self.client, "
                      "camera, start_time)\n"
                      "        for removed in await async_prune_previews("
                      "self.hass, camera):", self.COORDINATOR)

    def test_it_is_not_the_retention_number(self):
        source = (Path(__file__).parents[1] / "custom_components" /
                  "tapo_h500" / "media.py").read_text()
        self.assertIn("surplus(strays, PREVIEW_KEEP)", source)


if __name__ == "__main__":
    unittest.main()
