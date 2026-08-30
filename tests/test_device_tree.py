"""The cameras belong to the hub, and the device registry should say so.

A TD21 reaches Home Assistant only through the hub: no address of its own, no
Wi-Fi, a sub-GHz radio link and nothing else. Without `via_device` they sat in
the device list as the hub's peers, so nothing said that unplugging the hub
takes all of them with it, and the hub's own page did not list what depends on
it.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

entity_mod = importlib.import_module("tapo_h500.entity")
sensor_mod = importlib.import_module("tapo_h500.sensor")
const = importlib.import_module("tapo_h500.const")
DOMAIN = const.DOMAIN

CAMERA = {"device_id": "cam0", "alias": "Front", "device_model": "TD21"}


class WhereACameraHangs(unittest.TestCase):
    def setUp(self):
        self.coord, _ = harness._build()
        self.entry = harness._Entry(20)
        self.coord.entry = self.entry

    def _info(self):
        return entity_mod.H500Entity(self.coord, 0, CAMERA)._attr_device_info

    def test_it_hangs_off_the_hub(self):
        self.assertEqual(self._info()["via_device"], (DOMAIN, "test"))

    def test_the_link_points_at_the_hubs_own_identifier(self):
        """A via_device naming something no device claims is silently
        ignored, and the tree looks exactly as flat as before."""
        hub = sensor_mod.hub_device(self.coord, self.entry)
        self.assertIn(self._info()["via_device"], hub["identifiers"])

    def test_the_camera_keeps_its_own_identity(self):
        info = self._info()
        self.assertEqual(info["identifiers"], {(DOMAIN, "cam0")})
        self.assertEqual(info["name"], "Front")
        self.assertEqual(info["model"], "TD21")

    def test_two_hubs_do_not_share_a_parent(self):
        """The link is the entry, so a second hub's cameras hang off the
        second hub."""
        other, _ = harness._build()
        other_entry = harness._Entry(20)
        other_entry.entry_id = "second"
        other.entry = other_entry
        second = entity_mod.H500Entity(
            other, 0, {"device_id": "cam9", "alias": "Shed"})
        self.assertNotEqual(second._attr_device_info["via_device"],
                            self._info()["via_device"])

    def test_the_hub_hangs_off_nothing(self):
        """It is the root. A via_device on it would be a device pointing at
        itself."""
        self.assertNotIn("via_device",
                         sensor_mod.hub_device(self.coord, self.entry))


if __name__ == "__main__":
    unittest.main()
