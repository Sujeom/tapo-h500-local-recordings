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

    def __init__(self, chunks: int):
        self.chunks = chunks
        self.sessions = 0
        self.windows: list[tuple[int, int]] = []
        self.completed = 0
        self.delivered = 0

    async def iter_recording(self, camera, start_time, end_time,
                             kind="download"):
        assert kind == "preview", kind
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


if __name__ == "__main__":
    unittest.main()
