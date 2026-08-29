"""A repair system that has stopped working says so.

Every notice this integration raises -- storage nearly full, hub unreachable,
media wedged, camera silent -- comes through one call in the poll. It is
wrapped so a failure cannot kill the poll, which is right, but it was logged
at debug: the notices could stop entirely and the only trace was a line nobody
reads. That is exactly how the storage warning stayed dead for months.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

repairs = importlib.import_module("tapo_h500.repairs")
LOGGER = "tapo_h500.coordinator"


def poll(coord):
    coord.data = asyncio.run(coord._async_update_data())


class _Broken:
    """Stands in for async_check and refuses, the way a bad check would."""

    def __init__(self):
        self.calls = 0

    def __call__(self, hass, entry_id, coordinator):
        self.calls += 1
        raise RuntimeError("a check blew up")


class WhenTheChecksFail(unittest.TestCase):
    def setUp(self):
        self.original = repairs.async_check
        self.addCleanup(setattr, repairs, "async_check", self.original)

    def test_the_first_failure_is_a_warning(self):
        coord, _ = harness._build()
        repairs.async_check = _Broken()
        with self.assertLogs(LOGGER, level="WARNING") as caught:
            poll(coord)
        self.assertTrue(any("not being updated" in line
                            for line in caught.output), caught.output)
        self.assertTrue(coord._repairs_broken)

    def test_it_is_not_repeated_every_two_seconds(self):
        """A warning per poll is its own way of being unreadable."""
        coord, _ = harness._build()
        repairs.async_check = _Broken()
        with self.assertLogs(LOGGER, level="WARNING"):
            poll(coord)
        with self.assertNoLogs(LOGGER, level="WARNING"):
            poll(coord)
            poll(coord)

    def test_the_poll_still_completes(self):
        """The wrapping is there so a broken check cannot take the hub's
        readings down with it, and that must stay true."""
        coord, _ = harness._build()
        repairs.async_check = _Broken()
        poll(coord)
        self.assertIsNotNone(coord.data)

    def test_recovery_is_announced_and_only_once(self):
        coord, _ = harness._build()
        broken = _Broken()
        repairs.async_check = broken
        with self.assertLogs(LOGGER, level="WARNING"):
            poll(coord)
        repairs.async_check = lambda hass, entry_id, coordinator: None
        with self.assertLogs(LOGGER, level="WARNING") as caught:
            poll(coord)
        self.assertTrue(any("again" in line for line in caught.output),
                        caught.output)
        self.assertFalse(coord._repairs_broken)
        with self.assertNoLogs(LOGGER, level="WARNING"):
            poll(coord)

    def test_a_healthy_run_says_nothing_at_all(self):
        coord, _ = harness._build()
        repairs.async_check = lambda hass, entry_id, coordinator: None
        with self.assertNoLogs(LOGGER, level="WARNING"):
            poll(coord)
        self.assertFalse(coord._repairs_broken)


if __name__ == "__main__":
    unittest.main()
