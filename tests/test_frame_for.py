"""One event's own frame, fetched once and shared.

The latest-event picture used to serve whatever clip was newest in the index.
Once a notification's Image button opens that picture's dialog, the picture
has to be the event the notification named -- so the coordinator can fetch
the frame of one specific clip, with the same one-attempt guard the newest
frame has, keyed by clip so the two cannot cancel each other's attempt.
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

# The suite registers the component as `tapo_h500`; patching a second copy
# imported under its directory name would leave the coordinator untouched.
coordinator_mod = importlib.import_module("tapo_h500.coordinator")
media = importlib.import_module("tapo_h500.media")

CAMERA = {"device_id": "dev-1", "alias": "Front Doorbell"}
NOW = 1_786_600_000


class OneEventsOwnFrame(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent / "_frame_for_tmp"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.coord, _ = harness._build()
        self.coord.hass.config = type("C", (), {
            "media_dirs": {"local": str(self.root)}})()
        self.fetched = []

        async def fetch(camera, start_time):
            """What the preview fetch does: write the frame where the
            download would have. A start of 0 stands in for a refusal."""
            self.fetched.append(start_time)
            if start_time:
                path = media.clip_path(self.coord.hass, camera, start_time, ".jpg")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"frame {start_time}".encode())

        self.coord._fetch_frame = fetch
        # The suite's media stub blanks the download coroutines; the fallback
        # here is the real newest-on-disk scan, against the temp media root.
        self.addCleanup(setattr, coordinator_mod, "async_latest_image",
                        coordinator_mod.async_latest_image)
        coordinator_mod.async_latest_image = ha_stubs._real_media_attr(
            "async_latest_image")

    def _frame(self, start):
        return asyncio.run(self.coord.async_frame_for(0, CAMERA, start))

    def test_it_is_the_frame_of_that_clip(self):
        self.assertEqual(self._frame(NOW - 600), b"frame 1786599400")

    def test_one_fetch_however_many_ask(self):
        async def two_at_once():
            return await asyncio.gather(
                self.coord.async_frame_for(0, CAMERA, NOW - 600),
                self.coord.async_frame_for(0, CAMERA, NOW - 600))
        first, second = asyncio.run(two_at_once())
        self.assertEqual(first, second)
        self.assertEqual(self.fetched, [NOW - 600], "shared, not repeated")

    def test_a_second_clip_gets_its_own_attempt(self):
        """Keyed by clip, so a look at this event and a look at the newest one
        do not take turns cancelling each other's attempt -- the failure a
        single per-camera slot would have had."""
        self._frame(NOW - 600)
        self._frame(NOW - 60)
        self._frame(NOW - 600)
        self.assertEqual(self.fetched, [NOW - 600, NOW - 60])

    def test_a_refused_frame_falls_back_to_the_newest_on_disk(self):
        """A picture of the wrong moment beats a dialog with nothing in it,
        and it is what this entity always showed before."""
        self._frame(NOW - 600)
        self.assertEqual(self._frame(0), b"frame 1786599400")

    def test_attempts_older_than_the_window_are_forgotten(self):
        """The hub no longer lists them, so nothing will ask again; the
        record would otherwise grow by one entry per clip forever."""
        self._frame(NOW - 3 * 86400)
        self._frame(NOW - 60)
        self.assertEqual(sorted(self.coord._frame_attempts),
                         [(0, NOW - 60)])


if __name__ == "__main__":
    unittest.main()
