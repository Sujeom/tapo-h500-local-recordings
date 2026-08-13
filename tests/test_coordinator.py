"""Poll ordering and cadence, without a Home Assistant runtime.

Home Assistant is stubbed to the handful of names coordinator.py imports, so
the poll sequence can be driven directly and its call order observed. What is
being protected here is latency: hub status used to be fetched before the
detection lookups, which put a round trip in front of every notification.
"""
import asyncio
import importlib
import re
import sys
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"


class _StubCoordinatorBase:
    """Enough DataUpdateCoordinator for the subclass to construct and run."""

    def __init__(self, hass, logger, name=None, config_entry=None,
                 update_interval=None):
        self.hass = hass
        self.config_entry = config_entry
        self.update_interval = update_interval

    # DataUpdateCoordinator[dict[...]] is subscripted at class definition.
    def __class_getitem__(cls, item):
        return cls


def _install_stubs():
    ha = types.ModuleType("homeassistant")
    mods = {
        "homeassistant": ha,
        "homeassistant.config_entries": ("ConfigEntry", type("ConfigEntry", (), {})),
        "homeassistant.core": ("HomeAssistant", type("HomeAssistant", (), {})),
        "homeassistant.exceptions": ("HomeAssistantError",
                                     type("HomeAssistantError", (Exception,), {})),
    }
    for path, attr in mods.items():
        if path == "homeassistant":
            sys.modules[path] = ha
            continue
        module = types.ModuleType(path)
        setattr(module, attr[0], attr[1])
        sys.modules[path] = module

    dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
    dispatcher.sent = []
    dispatcher.async_dispatcher_send = lambda hass, signal, *a: (
        dispatcher.sent.append((signal, a)))
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = _StubCoordinatorBase
    update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})
    helpers = types.ModuleType("homeassistant.helpers")
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")

    class _Now:
        @staticmethod
        def timestamp():
            return 1_786_600_000
    dt.utcnow = lambda: _Now()
    util.dt = dt
    sys.modules.update({
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.dispatcher": dispatcher,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
    })

    # media.py pulls in more of Home Assistant than this needs; the download
    # path is not what these tests exercise.
    media = types.ModuleType("tapo_h500.media")
    media.async_download_clip = None
    media.async_prune = None
    media.existing_clip = lambda *a, **k: None
    package = types.ModuleType("tapo_h500")
    package.__path__ = [str(COMPONENT)]
    sys.modules["tapo_h500"] = package
    sys.modules["tapo_h500.media"] = media
    return dispatcher


DISPATCHER = _install_stubs()
coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")


class _Hass:
    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class _Entry:
    entry_id = "test"

    def __init__(self, interval):
        self.options = {"poll_interval": interval}


class _Client:
    """Records the order calls arrive in."""

    def __init__(self):
        self.calls = []

    def cameras(self):
        self.calls.append("cameras")
        return [{"device_id": "cam0", "alias": "Front"}]

    def recent(self, camera, start, end):
        self.calls.append("recent")
        return []

    def detections(self, camera, start, end):
        self.calls.append("detections")
        return []

    def hub_status(self):
        self.calls.append("hub_status")
        return {}


def _build(interval=20):
    """A 20s interval by default: STATUS_MAX_AGE 60 / 20 makes status every 3rd
    poll, which keeps the cadence assertions short and readable."""
    client = _Client()
    coord = coordinator_mod.H500Coordinator(_Hass(), _Entry(interval), client)
    # The download path is out of scope; neutralise it.
    coord._download_new = lambda *a, **k: None
    return coord, client


class PollOrdering(unittest.TestCase):
    def test_events_are_fetched_before_hub_status(self):
        """Status must not sit in front of the detection lookup.

        This is the whole latency fix. If hub_status runs first, every
        notification waits on a round trip fetching LED state and storage.
        """
        coord, client = _build()
        asyncio.run(coord._async_update_data())
        self.assertIn("hub_status", client.calls)
        self.assertLess(client.calls.index("detections"),
                        client.calls.index("hub_status"))

    def test_status_is_not_fetched_on_every_poll(self):
        """Polls 0 and 3 fetch it; 1, 2, 4 and 5 do not."""
        coord, client = _build()
        fetched = []
        for _ in range(6):
            before = client.calls.count("hub_status")
            asyncio.run(coord._async_update_data())
            fetched.append(client.calls.count("hub_status") > before)
        self.assertEqual(fetched, [True, False, False, True, False, False])

    def test_first_poll_still_fetches_status(self):
        """Nothing may be blank on startup waiting for poll 3."""
        coord, client = _build()
        asyncio.run(coord._async_update_data())
        self.assertIn("hub_status", client.calls)

    def test_a_status_failure_does_not_fail_the_poll(self):
        """Status is a bonus; events must survive it raising."""
        coord, client = _build()

        def boom():
            client.calls.append("hub_status")
            raise RuntimeError("hub busy")
        client.hub_status = boom
        result = asyncio.run(coord._async_update_data())
        self.assertIn("clips", result)


class CameraList(unittest.TestCase):
    def test_the_camera_list_is_not_refetched_every_poll(self):
        """58ms, more than the detection lookups it precedes, for a list that
        changes only when a camera is paired or removed."""
        coord, client = _build(interval=20)   # 300 / 20 -> every 15th poll
        for _ in range(6):
            asyncio.run(coord._async_update_data())
        self.assertEqual(client.calls.count("cameras"), 1)
        # ...but the detections still happen on every one of those polls.
        self.assertEqual(client.calls.count("detections"), 6)

    def test_a_failed_refresh_keeps_the_cached_list(self):
        """Losing a refresh must not blind the integration to its cameras."""
        coord, client = _build(interval=20)
        asyncio.run(coord._async_update_data())

        def boom():
            raise RuntimeError("hub busy")
        client.cameras = boom
        coord._polls = 0                       # force a refresh attempt
        asyncio.run(coord._async_update_data())
        self.assertEqual(len(coord.cameras), 1)

    def test_failing_with_nothing_cached_is_still_fatal(self):
        """A first poll that cannot list cameras has no data to work from."""
        coord, client = _build()

        def boom():
            raise RuntimeError("hub unreachable")
        client.cameras = boom
        with self.assertRaises(Exception):
            asyncio.run(coord._async_update_data())


class PollInterval(unittest.TestCase):
    def test_default_interval_is_short_enough_to_notify_promptly(self):
        """The interval is the entire notification latency budget. The hub
        answers a detection lookup in 19ms, so seconds here are all ours."""
        self.assertLessEqual(const.DEFAULT_POLL_INTERVAL, 3)

    def test_the_default_interval_is_one_the_options_form_accepts(self):
        """The form once enforced min=5 while the default was 2, so the
        default could not be re-saved. Read the bound out of config_flow.py
        rather than restating it, or the two drift apart again.
        """
        source = (COMPONENT / "config_flow.py").read_text()
        bound = re.search(r"vol\.Range\(min=(\d+), max=(\d+)\)", source)
        low, high = int(bound.group(1)), int(bound.group(2))
        self.assertGreaterEqual(const.DEFAULT_POLL_INTERVAL, low)
        self.assertLessEqual(const.DEFAULT_POLL_INTERVAL, high)

    def test_refresh_ages_survive_a_changed_interval(self):
        """Status every Nth poll must track the configured interval, not a
        fixed count -- otherwise a 60s interval means status every 30min."""
        fast, _ = _build(interval=2)
        slow, _ = _build(interval=60)
        self.assertEqual(fast._status_every, round(const.STATUS_MAX_AGE / 2))
        self.assertEqual(slow._status_every, 1)


if __name__ == "__main__":
    unittest.main()
