"""The preview endpoint answers a dashboard tile, never a stack trace.

This is the integration's only HTTP view and it had no test at all -- the
suite could not even import it until Home Assistant was stubbed properly.
`async_preview_clip` catches its own download failures, but the lines that set
the session up run above that guard, and a start_time outside the hub's
retention reaches them. Unhandled, a recordings card asking for a thumbnail
got a 500 and a traceback in the log.
"""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

preview = importlib.import_module("tapo_h500.preview")
const = importlib.import_module("tapo_h500.const")

CAMERA = {"device_id": "cam0", "alias": "Front"}
ENTRY = "e1"


class _Client:
    def __init__(self, camera=CAMERA, explode=False):
        self._camera, self._explode = camera, explode

    def camera_at(self, index):
        if self._explode:
            raise RuntimeError("no such camera")
        return self._camera


class _Hass:
    def __init__(self, client):
        self.data = {const.DOMAIN: {const.DATA_HUBS: {
            ENTRY: types.SimpleNamespace(client=client)}}}

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class _Request:
    def __init__(self, hass):
        self.app = {preview.KEY_HASS: hass}


def get(hass, camera_index="0", start_time="100"):
    view = preview.H500PreviewView()
    return asyncio.run(view.get(_Request(hass), ENTRY, camera_index,
                                start_time))


class BadInputIsRefusedPolitely(unittest.TestCase):
    def test_a_non_numeric_index_is_a_400(self):
        self.assertEqual(get(_Hass(_Client()), camera_index="../etc").status,
                         400)

    def test_a_negative_start_is_a_400(self):
        self.assertEqual(get(_Hass(_Client()), start_time="-1").status, 400)

    def test_an_unknown_entry_is_a_404(self):
        hass = _Hass(_Client())
        hass.data[const.DOMAIN][const.DATA_HUBS] = {}
        self.assertEqual(get(hass).status, 404)

    def test_an_unknown_camera_is_a_404(self):
        self.assertEqual(get(_Hass(_Client(explode=True))).status, 404)


class AFailedPreviewIsNotAServerError(unittest.TestCase):
    """The defect this file exists for."""

    def _with_preview(self, replacement):
        original = preview.async_preview_clip
        preview.async_preview_clip = replacement
        self.addCleanup(setattr, preview, "async_preview_clip", original)

    def test_a_preview_that_raises_becomes_a_404(self):
        async def explode(hass, client, camera, start):
            # Exactly what an out-of-retention start_time does: the failure
            # happens setting the session up, above the guard inside.
            raise OSError("no such directory")
        self._with_preview(explode)
        response = get(_Hass(_Client()))
        self.assertEqual(response.status, 404)
        self.assertEqual(response.text, "No preview available")

    def test_a_preview_that_declines_is_still_a_404(self):
        async def declines(hass, client, camera, start):
            return None
        self._with_preview(declines)
        self.assertEqual(get(_Hass(_Client())).status, 404)

    def test_a_frame_pruned_before_it_is_read_is_a_404(self):
        """Retention runs on its own schedule and does not know a request is
        in flight, so the file can go between being made and being read."""
        class _Vanished:
            def read_bytes(self):
                raise FileNotFoundError("pruned")
        async def vanishing(hass, client, camera, start):
            return _Vanished()
        self._with_preview(vanishing)
        self.assertEqual(get(_Hass(_Client())).status, 404)

    def test_a_good_preview_still_returns_the_frame(self):
        class _Frame:
            def read_bytes(self):
                return b"\xff\xd8jpeg"
        async def works(hass, client, camera, start):
            return _Frame()
        self._with_preview(works)
        response = get(_Hass(_Client()))
        self.assertEqual(response.body, b"\xff\xd8jpeg")
        self.assertEqual(response.content_type, "image/jpeg")


if __name__ == "__main__":
    unittest.main()
