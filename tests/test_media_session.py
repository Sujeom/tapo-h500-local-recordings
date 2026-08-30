"""One media session, opened once and finished properly.

Every recording download and every preview opens a fresh authenticated session
on port 8800 -- TCP connect, digest challenge, AES key exchange -- and the hub
only regards one as over when it has sent its own

    notification -> event_type=stream_status -> status=finished

A preview used to stop reading as soon as it had enough bytes for one frame,
which closes the socket without that notification ever arriving. The docstring
claimed that "unwinds the media session cleanly"; it does not. The socket does
close, but late and non-deterministically -- the generator is only finalised
when the event loop gets round to it, and until then it is still holding the
client's media lock.

So these assert the shape of the lifecycle rather than the byte count: exactly
one session per preview, the same two-second window as before, the same data
handed to ffmpeg, and the iterator run to its natural end.
"""
import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402,F401  (installs the HA stubs)


def _stub_media_dependencies():
    """The parts of Home Assistant media.py imports and these tests do not."""
    ffmpeg = types.ModuleType("homeassistant.components.ffmpeg")
    ffmpeg.get_ffmpeg_manager = lambda hass: types.SimpleNamespace(
        binary="/nonexistent/ffmpeg")
    sys.modules.setdefault("homeassistant.components.ffmpeg", ffmpeg)

    auth = types.ModuleType("homeassistant.components.http.auth")
    auth.async_sign_path = lambda hass, path, lifetime: path
    http = types.ModuleType("homeassistant.components.http")
    http.auth = auth
    sys.modules.setdefault("homeassistant.components.http", http)
    sys.modules.setdefault("homeassistant.components.http.auth", auth)


def _real_media():
    """The actual module, without taking it away from anyone else.

    test_coordinator installs a hollow ``tapo_h500.media`` so the coordinator
    imports without ffmpeg, and two other test modules reach into whatever is
    in ``sys.modules`` under that name and patch it. Leaving the real one
    there instead makes this file's position in the alphabet decide whether
    those pass. So: import it, then put the hollow one back.
    """
    hollow = sys.modules.pop("tapo_h500.media", None)
    try:
        return importlib.import_module("tapo_h500.media")
    finally:
        if hollow is not None:
            sys.modules["tapo_h500.media"] = hollow
            sys.modules["tapo_h500"].media = hollow


_stub_media_dependencies()
media = _real_media()
const = importlib.import_module("tapo_h500.const")

CAMERA = {"device_id": "cam0", "mac": "AABB", "alias": "Front", "channel_id": 0}
START = 1_786_600_000
CHUNK = b"\x47" * 8192


class _Hass:
    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class _Client:
    """One media session per iter_recording call, like the real client.

    ``completed`` only becomes true when the generator reaches its own return,
    which is exactly what a consumer that abandons it never lets happen.
    """

    def __init__(self, chunks: int, expect_kind: str = "preview"):
        self.chunks = chunks
        self.expect_kind = expect_kind
        self.sessions = 0
        self.windows: list[tuple[int, int]] = []
        self.completed = 0
        self.delivered = 0

    async def iter_recording(self, camera, start_time, end_time,
                             kind="download"):
        assert kind == self.expect_kind, kind
        self.sessions += 1
        self.windows.append((start_time, end_time))
        for _ in range(self.chunks):
            self.delivered += len(CHUNK)
            yield CHUNK
        # Standing in for the hub's "finished" notification: reached only if
        # the caller keeps reading to the end of the bounded window.
        self.completed += 1


class PreviewSession(unittest.TestCase):
    # Enough chunks to cross the cap with room to spare, so "stopped at the
    # cap" and "read to the end" are clearly different numbers.
    CHUNKS = (const.PREVIEW_MAX_BYTES // len(CHUNK)) + 8

    def _patch(self, name, value):
        self.addCleanup(setattr, media, name, getattr(media, name))
        setattr(media, name, value)

    def _preview(self, chunks=None):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self._patch("media_root", lambda hass: Path(root.name))
        client = _Client(self.CHUNKS if chunks is None else chunks)
        written: list[int] = []

        async def measure(_hass, args):
            # ffmpeg is not the subject; record what it was handed instead.
            written.append(Path(args[args.index("-i") + 1]).stat().st_size)
            return False

        self._patch("_run_ffmpeg", measure)
        result = asyncio.run(
            media.async_preview_clip(_Hass(), client, CAMERA, START))
        return client, written, result

    def test_preview_drains_the_bounded_stream(self):
        """The hub, not the client, decides when a session is over."""
        client, _, _ = self._preview()
        self.assertEqual(client.completed, 1,
                         "the media session was abandoned before the hub "
                         "could report it finished")

    def test_it_still_opens_exactly_one_session(self):
        client, _, _ = self._preview()
        self.assertEqual(client.sessions, 1)

    def test_it_still_asks_for_only_the_opening_seconds(self):
        """Draining must not become downloading the whole recording."""
        client, _, _ = self._preview()
        self.assertEqual(client.windows,
                         [(START, START + const.PREVIEW_SECONDS)])

    def test_the_tail_is_discarded_rather_than_written(self):
        """One frame is the whole point; the rest is not kept."""
        client, written, _ = self._preview()
        self.assertTrue(written, "ffmpeg was never given anything")
        # Bounded at the cap plus at most the chunk that crossed it, which is
        # what the byte-capped version wrote too.
        self.assertLessEqual(written[0], const.PREVIEW_MAX_BYTES + len(CHUNK))
        self.assertGreaterEqual(written[0], const.PREVIEW_MAX_BYTES)
        self.assertLess(written[0], client.delivered,
                        "the discarded tail was written to disk")

    def test_a_short_stream_is_unaffected(self):
        """Most previews never reach the cap at all."""
        client, written, _ = self._preview(chunks=2)
        self.assertEqual(client.completed, 1)
        self.assertEqual(written[0], 2 * len(CHUNK))

    def test_a_stream_with_no_video_returns_nothing(self):
        client, written, result = self._preview(chunks=0)
        self.assertEqual(client.completed, 1)
        self.assertIsNone(result)
        self.assertEqual(written, [], "ffmpeg was run on an empty file")

    def test_the_docstring_no_longer_claims_breaking_is_clean(self):
        """The comment that justified the bug, so it cannot come back."""
        self.assertNotIn("unwinds the media session cleanly",
                         media.async_preview_clip.__doc__)

    def test_a_generated_frame_is_what_the_camera_then_serves(self):
        """The whole point of fetching it: the preview writes its frame at
        exactly the path the download would use, so the newest-thumbnail scan
        behind the camera entity picks it up and an older frame loses."""
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self._patch("media_root", lambda hass: Path(root.name))

        async def fake_ffmpeg(_hass, args):
            Path(args[-1]).write_bytes(b"fresh-frame")
            return True

        self._patch("_run_ffmpeg", fake_ffmpeg)
        hass = _Hass()
        # An older event's frame, already downloaded, on an earlier day.
        stale = media.clip_path(hass, CAMERA, START - 86400, ".jpg")
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"stale-frame")

        made = asyncio.run(media.async_preview_clip(
            hass, _Client(2), CAMERA, START))
        self.assertEqual(made, media.clip_path(hass, CAMERA, START, ".jpg"))
        self.assertEqual(
            asyncio.run(media.async_latest_image(hass, CAMERA)),
            b"fresh-frame")


if __name__ == "__main__":
    unittest.main()


class DetectionSidecar(unittest.TestCase):
    """What a download knew about its clip survives beside it on disk.

    The coordinator's index only reaches back a day, so anything that wants
    to answer "which of these files has a person in it" a week later needs
    the classification written down at the one moment it existed: download
    time. One small JSON per clip, deleted whenever the clip is.
    """

    def _hass(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        for name in ("media_root",):
            self.addCleanup(setattr, media, name, getattr(media, name))
        media.media_root = lambda hass: Path(root.name)

        async def fake_ffmpeg(_hass, args):
            Path(args[-1]).write_bytes(b"made")
            return True

        self.addCleanup(setattr, media, "_run_ffmpeg", media._run_ffmpeg)
        media._run_ffmpeg = fake_ffmpeg
        return _Hass()

    def _download(self, hass, detected):
        return asyncio.run(media.async_download_clip(
            hass, _Client(2, "download"), CAMERA, START, START + 15,
            convert=False, detected=detected))

    def test_the_types_are_written_beside_the_clip(self):
        import json
        hass = self._hass()
        self._download(hass, [2, 6, 17])
        sidecar = media.clip_path(hass, CAMERA, START, ".json")
        self.assertEqual(json.loads(sidecar.read_text()),
                         {"detection_types": [2, 6, 17]})

    def test_faces_ride_along_when_the_clip_carried_them(self):
        """The hub's index reaches back a day; the sidecar is the only
        place "when did Alice last come" can be answered from next month."""
        import json
        hass = self._hass()
        asyncio.run(media.async_download_clip(
            hass, _Client(2, "download"), CAMERA, START, START + 15,
            convert=False, detected=[6, 20], faces=[272465657857]))
        sidecar = media.clip_path(hass, CAMERA, START, ".json")
        self.assertEqual(json.loads(sidecar.read_text()),
                         {"detection_types": [6, 20],
                          "face_ids": [272465657857]})

    def test_no_faces_writes_no_face_key(self):
        import json
        hass = self._hass()
        self._download(hass, [2])
        sidecar = media.clip_path(hass, CAMERA, START, ".json")
        self.assertNotIn("face_ids", json.loads(sidecar.read_text()))

    def test_an_unclassified_download_writes_nothing(self):
        hass = self._hass()
        self._download(hass, None)
        self.assertFalse(media.clip_path(hass, CAMERA, START, ".json").exists())

    def test_deleting_the_clip_removes_the_sidecar(self):
        hass = self._hass()
        self._download(hass, [6])
        asyncio.run(media.async_delete_clip(hass, CAMERA, START))
        self.assertFalse(media.clip_path(hass, CAMERA, START, ".json").exists())

    def test_pruning_removes_the_sidecar_too(self):
        """An orphan sidecar would classify a clip that no longer exists."""
        hass = self._hass()
        for offset in (0, 100, 200):
            asyncio.run(media.async_download_clip(
                hass, _Client(2, "download"), CAMERA, START + offset, START + offset + 15,
                convert=False, detected=[6]))
        asyncio.run(media.async_prune(hass, CAMERA, keep=1))
        remaining = sorted(
            path.name for path in
            media.camera_dir(hass, CAMERA).glob("*/*.json"))
        self.assertEqual(len(remaining), 1)


class Backfill(unittest.TestCase):
    """Sidecars for the archive that predates them.

    Clips downloaded before sidecars existed appear in no type folder. The
    hub's own detection log answers for old windows -- the calendar already
    queries a month back -- so one service walks the disk, asks the hub what
    triggered each unclassified day, and writes the missing files. One
    detection query per camera-day that actually needs one; a day already
    fully classified costs the hub nothing.
    """

    def _hass(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.addCleanup(setattr, media, "media_root", media.media_root)
        media.media_root = lambda hass: Path(root.name)
        return _Hass()

    class _LogClient:
        def __init__(self, log):
            self.log = log            # start -> detection record
            self.windows = []

        def detections(self, camera, start, end):
            self.windows.append((start, end))
            return [record for moment, record in self.log.items()
                    if start <= moment <= end]

    def _clip(self, hass, start, sidecar=None):
        video = media.clip_path(hass, CAMERA, start, ".mp4")
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"v")
        if sidecar is not None:
            video.with_suffix(".json").write_text(
                __import__("json").dumps({"detection_types": sidecar}))

    def test_missing_sidecars_are_written_from_the_log(self):
        import json
        hass = self._hass()
        self._clip(hass, START)
        client = self._LogClient(
            {START: {"start_time": START, "events_1": (1 << 5) | (1 << 16),
                     "alarm_type": 17}})
        result = asyncio.run(media.async_classify_downloads(
            hass, client, CAMERA, days=31))
        self.assertEqual(result["written"], 1)
        sidecar = media.clip_path(hass, CAMERA, START, ".json")
        self.assertEqual(json.loads(sidecar.read_text()),
                         {"detection_types": [6, 17]})

    def test_the_backfill_carries_faces_too(self):
        import json
        hass = self._hass()
        self._clip(hass, START)
        client = self._LogClient(
            {START: {"start_time": START, "events_1": 1 << 19,
                     "event_info": [{"face_id": 99}]}})
        asyncio.run(media.async_classify_downloads(
            hass, client, CAMERA, days=31))
        sidecar = media.clip_path(hass, CAMERA, START, ".json")
        self.assertEqual(json.loads(sidecar.read_text())["face_ids"], [99])

    def test_one_second_of_index_tolerance(self):
        hass = self._hass()
        self._clip(hass, START)
        client = self._LogClient(
            {START + 1: {"start_time": START + 1, "events_1": 1 << 5}})
        result = asyncio.run(media.async_classify_downloads(
            hass, client, CAMERA, days=31))
        self.assertEqual(result["written"], 1)

    def test_a_day_already_classified_costs_the_hub_nothing(self):
        hass = self._hass()
        self._clip(hass, START, sidecar=[6])
        client = self._LogClient({})
        result = asyncio.run(media.async_classify_downloads(
            hass, client, CAMERA, days=31))
        self.assertEqual(client.windows, [])
        self.assertEqual(result["written"], 0)

    def test_a_clip_the_log_does_not_know_stays_unclassified(self):
        """No sidecar rather than a guessed one: absent means absent."""
        hass = self._hass()
        self._clip(hass, START)
        client = self._LogClient({})
        result = asyncio.run(media.async_classify_downloads(
            hass, client, CAMERA, days=31))
        self.assertEqual(result["written"], 0)
        self.assertFalse(
            media.clip_path(hass, CAMERA, START, ".json").exists())

    def test_an_unsupported_detection_log_is_a_clean_zero(self):
        hass = self._hass()
        self._clip(hass, START)

        class _NoLog:
            def detections(self, camera, start, end):
                return None

        result = asyncio.run(media.async_classify_downloads(
            hass, _NoLog(), CAMERA, days=31))
        self.assertEqual(result["written"], 0)

    def test_the_query_window_covers_the_clips_local_day(self):
        """Day folders are LOCAL dates (the harness pins -07:00), so the
        epoch window asked of the hub must cover that local day -- a UTC-day
        window silently misses every evening clip."""
        hass = self._hass()
        self._clip(hass, START)  # 22:46 local on 2026-08-12
        client = self._LogClient({START: {"start_time": START,
                                          "events_1": 1 << 5}})
        asyncio.run(media.async_classify_downloads(
            hass, client, CAMERA, days=31))
        (low, high), = client.windows
        self.assertLessEqual(low, START)
        self.assertGreaterEqual(high, START)

    def test_days_bound_the_walk(self):
        hass = self._hass()
        self._clip(hass, START - 40 * 86400)
        client = self._LogClient({})
        result = asyncio.run(media.async_classify_downloads(
            hass, client, CAMERA, days=7))
        self.assertEqual(client.windows, [])
        self.assertEqual(result["written"], 0)


class BackfillService(unittest.TestCase):
    INIT = (COMPONENT / "__init__.py").read_text()
    # The thirteen service handlers moved out of the package body.
    SERVICES_SRC = (COMPONENT / "services.py").read_text()
    SERVICES_YAML = (COMPONENT / "services.yaml").read_text()

    def test_the_service_walks_every_camera(self):
        body = self.SERVICES_SRC.split("async def classify_downloads", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("for camera in coordinator.cameras", body)
        self.assertIn("async_classify_downloads", body)

    def test_it_is_registered_and_documented(self):
        self.assertIn("SERVICE_CLASSIFY_DOWNLOADS", self.SERVICES_SRC)
        self.assertIn("classify_downloads:", self.SERVICES_YAML)

    def test_days_are_bounded_to_what_was_verified(self):
        self.assertIn("vol.Range(min=1, max=31)", self.SERVICES_SRC.split(
            "CLASSIFY_SCHEMA", 1)[1][:400])


class ArchiveFaceSearch(unittest.TestCase):
    """find_face reaches the archive, not just the hub's last day."""

    def _hass(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.addCleanup(setattr, media, "media_root", media.media_root)
        media.media_root = lambda hass: Path(root.name)
        return _Hass()

    def _clip(self, hass, start, face_ids=None):
        import json
        video = media.clip_path(hass, CAMERA, start, ".mp4")
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"v")
        payload = {"detection_types": [6]}
        if face_ids is not None:
            payload["face_ids"] = face_ids
        video.with_suffix(".json").write_text(json.dumps(payload))

    def test_it_finds_them_newest_first(self):
        hass = self._hass()
        self._clip(hass, START - 10 * 86400, face_ids=[99])
        self._clip(hass, START - 3 * 86400, face_ids=[99])
        self._clip(hass, START - 86400, face_ids=[7])
        found = media.archive_face_search(hass, CAMERA, {"99"})
        self.assertEqual([entry["start_time"] for entry in found],
                         [START - 3 * 86400, START - 10 * 86400])

    def test_a_clip_without_faces_never_matches(self):
        hass = self._hass()
        self._clip(hass, START - 86400)
        self.assertEqual(media.archive_face_search(hass, CAMERA, {"99"}), [])
