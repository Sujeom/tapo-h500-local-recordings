"""The expensive lookups must thin out while the hub is failing, not thicken.

Everything on a slow cadence -- the paired camera list, hub status, the media
port handshake, the firmware check -- is gated on `poll % every == 0`, and the
counter behind it advanced only where a poll ran to the end. So it stopped the
instant the hub stopped answering, and whichever gates were open at that
moment stayed open for every retry that followed: a device already failing got
its camera list re-fetched, its status read and its media port handshaken on
each one. The load went up exactly when it should have gone down.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)


class _Failing(harness._Client):
    """Answers for the camera list and nothing else, like a hub that is up
    but will not serve a search."""

    def recent(self, camera, start, end):
        self.calls.append("recent")
        raise OSError("hub not answering")

    def detections(self, camera, start, end):  # pragma: no cover - never got here
        self.calls.append("detections")
        raise OSError("hub not answering")


def _poll(coord):
    try:
        asyncio.run(coord._async_update_data())
    except Exception:
        pass


class WhileTheHubIsFailing(unittest.TestCase):
    def setUp(self):
        self.coord, _ = harness._build()
        self.client = _Failing()
        self.coord.client = self.client
        self.coord._download_new = lambda *a, **k: None
        # A cached list, so the poll gets past the camera fetch and dies in
        # the search -- the shape of a hub that is up and not answering.
        self.coord.cameras = [{"device_id": "cam0", "alias": "Front"}]

    def test_the_camera_list_is_not_refetched_on_every_retry(self):
        for _ in range(4):
            _poll(self.coord)
        self.assertEqual(self.client.calls.count("cameras"), 1,
                         "poll 0 opens every gate; polls 1-3 must not")

    def test_status_is_not_reread_on_every_retry(self):
        for _ in range(4):
            _poll(self.coord)
        self.assertEqual(self.client.calls.count("hub_status"), 0,
                         "the search raises before status is reached, and a "
                         "frozen counter would reopen the gate each time")

    def test_the_counter_advances_through_failure(self):
        before = self.coord._polls
        for _ in range(3):
            _poll(self.coord)
        self.assertEqual(self.coord._polls, before + 3)

    def test_recovery_resumes_the_cadence_where_it_left_off(self):
        """Not back at zero. A poll that starts working must not reopen every
        gate at once on a hub that has just come back."""
        for _ in range(4):
            _poll(self.coord)
        self.coord.client = harness._Client()
        asyncio.run(self.coord._async_update_data())
        self.assertEqual(self.coord.client.calls.count("cameras"), 0,
                         "poll 4 is not a multiple of the camera cadence")


class PollZeroStillDoesEverything(unittest.TestCase):
    """The counter is read before it is bumped, which is what keeps the
    first poll fetching the things that would otherwise be blank."""

    def test_the_first_poll_fetches_the_camera_list_and_status(self):
        coord, client = harness._build()
        asyncio.run(coord._async_update_data())
        self.assertEqual(client.calls.count("cameras"), 1)
        self.assertEqual(client.calls.count("hub_status"), 1)
        self.assertEqual(coord._polls, 1)


if __name__ == "__main__":
    unittest.main()
