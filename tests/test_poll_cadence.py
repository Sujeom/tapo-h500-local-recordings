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

import importlib  # noqa: E402

const = importlib.import_module("tapo_h500.const")
dt_util = sys.modules["homeassistant.util.dt"]
NOW = dt_util.utcnow().timestamp()


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


class WhileNothingIsHappening(unittest.TestCase):
    """Nearly all of this integration's traffic is asking a quiet hub whether
    anything happened yet. At two seconds that is 43,200 round trips a day
    against a device whose load is a live suspect in the wedge.
    """

    IDLE = const.POLL_IDLE_AFTER

    def setUp(self):
        # A five-second base, so "never faster than configured" and "six
        # seconds when quiet" are two different numbers.
        self.coord, self.client = harness._build(5)

    def _interval(self):
        return self.coord.update_interval.total_seconds()

    def _quiet_for(self, seconds):
        self.coord._last_activity_at = (
            NOW - seconds)

    def test_a_quiet_house_slows_the_poll_down(self):
        asyncio.run(self.coord._async_update_data())
        self._quiet_for(self.IDLE)
        asyncio.run(self.coord._async_update_data())
        self.assertEqual(self._interval(), const.POLL_IDLE_INTERVAL)

    def test_it_waits_the_full_stretch_first(self):
        """A doorbell that answers slowly for the first ten minutes of quiet
        would be slow for most of the times anyone uses it."""
        self._quiet_for(self.IDLE - 1)
        asyncio.run(self.coord._async_update_data())
        self.assertEqual(self._interval(), 5)

    def test_anything_new_snaps_it_back(self):
        self._quiet_for(self.IDLE)
        asyncio.run(self.coord._async_update_data())
        self.assertEqual(self._interval(), const.POLL_IDLE_INTERVAL)
        self.client.next_detections = [
            {"startTime": NOW - 5, "events_1": 2}]
        asyncio.run(self.coord._async_update_data())
        self.assertEqual(self._interval(), 5,
                         "somebody is at the door and more is coming")

    def test_it_never_polls_faster_than_configured(self):
        """Someone who chose thirty seconds asked for less traffic, not more."""
        coord, _ = harness._build(30)
        coord._last_activity_at = NOW - self.IDLE
        asyncio.run(coord._async_update_data())
        self.assertEqual(coord.update_interval.total_seconds(), 30)

    def test_a_failing_hub_outranks_a_quiet_one(self):
        """Backing off six seconds while a hub is unreachable would be the
        smaller of the two backoffs winning."""
        self.coord.client = _Failing()
        self._quiet_for(self.IDLE)
        _poll(self.coord)
        self.assertGreater(self._interval(), const.POLL_IDLE_INTERVAL)

    def test_a_fresh_start_is_not_a_quiet_house(self):
        """Zero would make every restart begin backed off, which is the
        opposite of what a restart needs."""
        coord, _ = harness._build(5)
        self.assertEqual(coord._last_activity_at,
                         NOW)
