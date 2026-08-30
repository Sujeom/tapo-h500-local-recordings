"""A doorbell paired after setup gets its entities without a reload.

The hub's paired list is refreshed on a schedule, so the coordinator learns
about a new camera within minutes. Until this, nothing built its entities --
and pairing a second doorbell is the most likely thing anybody does after
setting the integration up, so what they saw was nothing happening.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

# Every platform that builds something per camera.
PER_CAMERA = ("binary_sensor", "calendar", "camera", "event", "image",
              "sensor")


def run(coro):
    return asyncio.run(coro)


def _camera(n):
    return {"device_id": f"cam{n}", "alias": f"C{n}", "device_model": "TD21"}


class _World(unittest.TestCase):
    def _setup(self, name, cameras=1):
        module = importlib.import_module(f"tapo_h500.{name}")
        coord, client = harness._build()
        coord.cameras = [_camera(n) for n in range(cameras)]
        client.siren_tones = lambda: ["Doorbell"]
        coord.client = client
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
        entry = coord.entry
        self.unloaders = []
        entry.async_on_unload = self.unloaders.append
        self.added = []
        run(module.async_setup_entry(hass, entry, self.added.extend))
        return coord

    @staticmethod
    def _fire(coord):
        """What a poll does once it has updated the data."""
        for listener in list(getattr(coord, "_listeners", {}).values()):
            listener[0]()


class APairedDoorbell(_World):
    def test_its_entities_appear_without_a_reload(self):
        for name in PER_CAMERA:
            with self.subTest(platform=name):
                coord = self._setup(name, cameras=1)
                before = len(self.added)
                coord.cameras.append(_camera(1))
                self._fire(coord)
                self.assertGreater(
                    len(self.added), before,
                    f"{name}: a second camera produced no entities")

    def test_the_new_entities_belong_to_the_new_camera(self):
        coord = self._setup("camera", cameras=1)
        coord.cameras.append(_camera(1))
        self._fire(coord)
        owners = {entity.camera["device_id"] for entity in self.added}
        self.assertEqual(owners, {"cam0", "cam1"})

    def test_nothing_is_added_twice(self):
        """The listener fires on every poll. Without a memory of what was
        served, a registry fills with hundreds of copies."""
        for name in PER_CAMERA:
            with self.subTest(platform=name):
                coord = self._setup(name, cameras=2)
                settled = len(self.added)
                for _ in range(5):
                    self._fire(coord)
                self.assertEqual(len(self.added), settled)

    def test_a_camera_that_goes_away_takes_nothing_with_it(self):
        """Removal is the device registry's business, not this listener's --
        and shrinking the list must not make it rebuild the survivors."""
        coord = self._setup("camera", cameras=2)
        settled = len(self.added)
        coord.cameras.pop()
        self._fire(coord)
        self.assertEqual(len(self.added), settled)

    def test_the_listener_lets_go_when_the_entry_unloads(self):
        """A listener outliving its entry holds the coordinator alive."""
        for name in PER_CAMERA:
            with self.subTest(platform=name):
                coord = self._setup(name, cameras=1)
                self.assertTrue(
                    self.unloaders,
                    f"{name}: nothing registered an unload for the listener")
                before = len(getattr(coord, "_listeners", {}))
                for unsubscribe in self.unloaders:
                    unsubscribe()
                self.assertLess(len(getattr(coord, "_listeners", {})), before)


if __name__ == "__main__":
    unittest.main()
