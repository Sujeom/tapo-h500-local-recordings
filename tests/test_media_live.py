"""The download pipeline and the archive, run against a real disk.

media.py held the largest uncovered block left: the download tail -- convert,
sidecar, cleanup -- plus export, verify, the sidecar backfill and the archive
face search. Everything here writes real files under a temporary media root
and, where ffmpeg is involved, runs the real ffmpeg: a fake one proves
nothing about whether a truncated clip is caught.
"""
import asyncio
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import ha_stubs  # noqa: E402

from homeassistant.exceptions import HomeAssistantError  # noqa: E402

media = ha_stubs.real_module("media")
dt_util = sys.modules["homeassistant.util.dt"]
NOW = int(dt_util.utcnow().timestamp())
CAMERA = {"device_id": "cam0", "alias": "Front"}
FFMPEG = shutil.which("ffmpeg")


def _valid_ts(path: Path) -> None:
    """A third of a second of real video, so decode checks mean something."""
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.3",
                    "-f", "mpegts", str(path)], check=True)


class _Hass(harness._Hass):
    def __init__(self, root: Path, allowed=()):
        super().__init__()
        self.config = type("C", (), {
            "media_dirs": {"local": str(root)},
            "is_allowed_path": staticmethod(
                lambda p, _ok=tuple(str(a) for a in allowed):
                any(str(p).startswith(prefix) for prefix in _ok)),
        })()


class _Client:
    """Streams whatever bytes the test loaded into it."""

    def __init__(self, chunks=(b"\x47" * 188,)):
        self.chunks = list(chunks)

    async def iter_recording(self, camera, start, end, kind="download"):
        for chunk in self.chunks:
            yield chunk


class _World(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / "_media_live_tmp"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.keep = self.root.parent / "_media_live_keep"
        shutil.rmtree(self.keep, ignore_errors=True)
        self.keep.mkdir()
        self.addCleanup(shutil.rmtree, self.keep, ignore_errors=True)
        self.hass = _Hass(self.root, allowed=[self.keep])

    def _download(self, client, convert=False, **kwargs):
        return asyncio.run(media.async_download_clip(
            self.hass, client, CAMERA, NOW, NOW + 15, convert, **kwargs))

    def _day_dir(self):
        return media.camera_dir(self.hass, CAMERA) / media.clip_path(
            self.hass, CAMERA, NOW, ".ts").parent.name


@unittest.skipIf(FFMPEG is None, "ffmpeg is not installed")
class Downloading(_World):
    def test_an_unconverted_download_lands_as_ts_with_its_sidecar(self):
        result = self._download(_Client(), detected=[2, 6], faces=[7])
        target = media.clip_path(self.hass, CAMERA, NOW, ".ts")
        self.assertTrue(target.is_file())
        self.assertEqual(result["bytes"], 188)
        sidecar = json.loads(target.with_suffix(".json").read_text())
        self.assertEqual(sidecar, {"detection_types": [2, 6],
                                   "face_ids": [7]})

    def test_an_unclassified_download_writes_no_sidecar(self):
        """Absent means absent, never guessed."""
        self._download(_Client())
        target = media.clip_path(self.hass, CAMERA, NOW, ".ts")
        self.assertFalse(target.with_suffix(".json").exists())

    def test_an_empty_stream_is_its_own_error_and_leaves_nothing(self):
        with self.assertRaises(media.EmptyRecordingError):
            self._download(_Client(chunks=[]))
        leftovers = list(self.root.rglob("*"))
        self.assertEqual([p for p in leftovers if p.is_file()], [],
                         "the temp file is gone even on failure")

    def test_garbage_that_cannot_convert_is_an_error_not_a_broken_mp4(self):
        with self.assertRaises(HomeAssistantError) as caught:
            self._download(_Client(chunks=[b"not video at all"]),
                           convert=True)
        self.assertIn("convert", str(caught.exception))
        self.assertEqual([p for p in self.root.rglob("*.part")], [])

    def test_real_video_converts_and_gets_a_thumbnail(self):
        source = self.root / "src.ts"
        _valid_ts(source)
        result = self._download(_Client(chunks=[source.read_bytes()]),
                                convert=True)
        target = media.clip_path(self.hass, CAMERA, NOW, ".mp4")
        self.assertTrue(target.is_file())
        self.assertTrue(target.with_suffix(".jpg").is_file(),
                        "the thumbnail is what every card shows")
        self.assertIn("thumbnail", result)


@unittest.skipIf(FFMPEG is None, "ffmpeg is not installed")
class Verifying(_World):
    def test_a_clip_that_decodes_passes(self):
        clip = self.root / "good.ts"
        _valid_ts(clip)
        self.assertTrue(asyncio.run(media.async_verify(self.hass, clip)))

    def test_a_truncated_clip_fails_while_it_can_still_be_refetched(self):
        """Right name, plausible size, no video: discovering this later
        means discovering the hub's copy is gone."""
        clip = self.root / "bad.ts"
        good = self.root / "good.ts"
        _valid_ts(good)
        clip.write_bytes(good.read_bytes()[:300])
        self.assertFalse(asyncio.run(media.async_verify(self.hass, clip)))


class Exporting(_World):
    def _seed(self, start=NOW):
        target = media.clip_path(self.hass, CAMERA, start, ".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video")
        target.with_suffix(".jpg").write_bytes(b"jpeg")
        return target

    def test_a_download_and_its_thumbnail_are_copied_in_the_same_layout(self):
        self._seed()
        result = asyncio.run(media.async_export(
            self.hass, CAMERA, NOW, str(self.keep)))
        self.assertEqual(result["count"], 2)
        copied = sorted(p.name for p in self.keep.rglob("*") if p.is_file())
        self.assertEqual(len(copied), 2)
        self.assertTrue(any(p.suffix == ".mp4"
                            for p in self.keep.rglob("*")))
        original = media.clip_path(self.hass, CAMERA, NOW, ".mp4")
        self.assertTrue(original.is_file(), "copied, never moved: the media "
                        "directory stays the working set")

    def test_an_undownloaded_clip_is_a_plain_refusal(self):
        with self.assertRaises(HomeAssistantError) as caught:
            asyncio.run(media.async_export(
                self.hass, CAMERA, NOW, str(self.keep)))
        self.assertIn("Download it first", str(caught.exception))

    def test_a_disallowed_destination_is_refused_by_name(self):
        """A service call must not reach the whole filesystem."""
        self._seed()
        with self.assertRaises(HomeAssistantError) as caught:
            asyncio.run(media.async_export(
                self.hass, CAMERA, NOW, "/etc"))
        self.assertIn("allowlist_external_dirs", str(caught.exception))


class Pruning(_World):
    def _seed(self, start):
        video = media.clip_path(self.hass, CAMERA, start, ".mp4")
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"v")
        video.with_suffix(".jpg").write_bytes(b"t")
        video.with_suffix(".json").write_text("{}")
        return video

    def test_the_oldest_go_with_their_thumbnail_and_sidecar(self):
        oldest = self._seed(NOW - 3000)
        for offset in (2000, 1000, 0):
            self._seed(NOW - offset)
        removed = asyncio.run(media.async_prune(self.hass, CAMERA, 3))
        self.assertEqual(len(removed), 3, "video, jpg and json together")
        self.assertFalse(oldest.exists())
        self.assertFalse(oldest.with_suffix(".jpg").exists())

    def test_protected_presses_survive_however_old(self):
        press = self._seed(NOW - 3000)
        for offset in (2000, 1000, 0):
            self._seed(NOW - offset)
        removed = asyncio.run(media.async_prune(
            self.hass, CAMERA, 3, protected={NOW - 3000}))
        self.assertTrue(press.exists())

    def test_zero_keeps_everything(self):
        self._seed(NOW)
        self.assertEqual(
            asyncio.run(media.async_prune(self.hass, CAMERA, 0)), [])


class TheSidecarBackfill(_World):
    def _video(self, day_offset, second, sidecar=None):
        start = NOW - day_offset * 86400 - second
        video = media.clip_path(self.hass, CAMERA, start, ".mp4")
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"v")
        if sidecar is not None:
            video.with_suffix(".json").write_text(json.dumps(sidecar))
        return start, video

    def _classify(self, detections_by_call, days=7):
        calls = []

        class _C:
            def detections(self, camera, low, high):
                calls.append((low, high))
                return detections_by_call.pop(0) if detections_by_call else []

        totals = asyncio.run(media.async_classify_downloads(
            self.hass, _C(), CAMERA, days))
        return totals, calls

    def test_a_bare_clip_gets_its_sidecar_from_the_log(self):
        start, video = self._video(0, 4000)
        totals, calls = self._classify([[
            {"start_time": start + 1, "alarm_type": 6,
             "events_1": (1 << 1) | (1 << 5),
             "event_info": [{"face_id": 7}]}]])
        self.assertEqual(totals, {"scanned": 1, "written": 1,
                                  "days_queried": 1})
        sidecar = json.loads(video.with_suffix(".json").read_text())
        self.assertEqual(sidecar["detection_types"], [2, 6])
        self.assertEqual(sidecar["face_ids"], [7])

    def test_a_clip_the_log_forgot_stays_unclassified(self):
        start, video = self._video(0, 4000)
        totals, _ = self._classify([[]])
        self.assertEqual(totals["written"], 0)
        self.assertFalse(video.with_suffix(".json").exists())

    def test_a_day_already_covered_costs_the_hub_nothing(self):
        self._video(0, 4000, sidecar={"detection_types": [2]})
        totals, calls = self._classify([])
        self.assertEqual(totals["days_queried"], 0)
        self.assertEqual(calls, [])

    def test_days_beyond_the_asked_window_are_left_alone(self):
        self._video(30, 4000)
        totals, calls = self._classify([], days=7)
        self.assertEqual(totals["days_queried"], 0)

    def test_a_stray_folder_is_not_a_date(self):
        (media.camera_dir(self.hass, CAMERA) / "not-a-date").mkdir(
            parents=True)
        totals, _ = self._classify([])
        self.assertEqual(totals["scanned"], 0)


class TheArchiveSearch(_World):
    def _seed(self, start, faces=None, broken=False, video=True):
        clip = media.clip_path(self.hass, CAMERA, start, ".mp4")
        clip.parent.mkdir(parents=True, exist_ok=True)
        if video:
            clip.write_bytes(b"v")
        sidecar = clip.with_suffix(".json")
        if broken:
            sidecar.write_text("{not json")
        else:
            payload = {"detection_types": [2, 6]}
            if faces:
                payload["face_ids"] = faces
            sidecar.write_text(json.dumps(payload))

    def _search(self, wanted):
        return media.archive_face_search(self.hass, CAMERA, set(wanted))

    def test_only_sidecars_naming_the_face_match_newest_first(self):
        self._seed(NOW - 7200, faces=[7])
        self._seed(NOW - 3600, faces=[8])
        self._seed(NOW - 60, faces=[7, 9])
        hits = self._search({"7"})
        self.assertEqual([hit["start_time"] for hit in hits],
                         [NOW - 60, NOW - 7200])
        self.assertEqual(hits[0]["detection_types"], [2, 6])

    def test_no_faces_in_the_sidecar_means_no_match(self):
        """Absent means absent, exactly as the type folders treat it."""
        self._seed(NOW - 60)
        self.assertEqual(self._search({"7"}), [])

    def test_a_broken_sidecar_is_skipped_not_fatal(self):
        self._seed(NOW - 7200, faces=[7])
        self._seed(NOW - 60, broken=True)
        self.assertEqual(len(self._search({"7"})), 1)

    def test_a_sidecar_whose_video_was_pruned_is_skipped(self):
        self._seed(NOW - 60, faces=[7], video=False)
        self.assertEqual(self._search({"7"}), [])

    def test_a_camera_that_never_downloaded_answers_empty(self):
        self.assertEqual(self._search({"7"}), [])


if __name__ == "__main__":
    unittest.main()
