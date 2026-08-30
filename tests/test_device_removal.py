"""An unpaired camera can be deleted, and a paired one cannot.

Unpair a camera from the hub and its Home Assistant device stays forever, with
its twenty-odd entities, every one of them unavailable and every one still in
every entity picker. Without this hook Home Assistant does not offer the
delete button at all: it refuses on the integration's behalf, assuming the
device would come straight back on the next poll.

Which is exactly right for a camera the hub still lists, and exactly wrong for
one it does not.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import ha_stubs  # noqa: E402

# The real package body. `tapo_h500` in sys.modules is the stub package, so
# importing it by name would give back something with no hook on it at all --
# which is what a test asserting the hook exists must not be able to do.
component = ha_stubs.real_module("init")
const = importlib.import_module("tapo_h500.const")
DOMAIN = const.DOMAIN


class _Device:
    def __init__(self, *identifiers):
        self.identifiers = set(identifiers)


def _hass(coordinator=None, entry_id="test"):
    hass = harness._Hass()
    hass.data = {DOMAIN: {const.DATA_HUBS: (
        {entry_id: coordinator} if coordinator is not None else {})}}
    return hass


def _may_remove(hass, device, entry_id="test"):
    entry = harness._Entry(20)
    entry.entry_id = entry_id
    return asyncio.run(component.async_remove_config_entry_device(
        hass, entry, device))


class WhatCanBeDeleted(unittest.TestCase):
    def setUp(self):
        self.coord, _ = harness._build()
        self.coord.cameras = [{"device_id": "cam0", "alias": "Front"},
                              {"device_id": "cam1", "alias": "Side"}]

    def test_a_camera_the_hub_still_lists_is_refused(self):
        """It would be recreated on the next poll, and a delete button that
        undoes itself is worse than one that is missing."""
        self.assertFalse(_may_remove(_hass(self.coord),
                                     _Device((DOMAIN, "cam0"))))

    def test_a_camera_the_hub_has_forgotten_can_go(self):
        self.assertTrue(_may_remove(_hass(self.coord),
                                    _Device((DOMAIN, "cam9"))))

    def test_the_hub_device_can_go(self):
        """Deleting it takes the config entry with it, which is what
        somebody clicking delete on a hub is asking for."""
        self.assertTrue(_may_remove(_hass(self.coord),
                                    _Device((DOMAIN, "test"))))

    def test_a_device_from_another_integration_is_not_ours_to_keep(self):
        self.assertTrue(_may_remove(_hass(self.coord),
                                    _Device(("other_domain", "cam0"))))

    def test_a_device_with_several_identifiers_is_kept_if_any_is_paired(self):
        """A registry entry can carry more than one, and one live camera is
        enough to make the whole device live."""
        self.assertFalse(_may_remove(
            _hass(self.coord),
            _Device(("other_domain", "x"), (DOMAIN, "cam1"))))

    def test_an_unloaded_entry_lets_everything_go(self):
        """With no coordinator there is nothing to contradict, and a device
        nobody can confirm is the user's to remove."""
        self.assertTrue(_may_remove(_hass(None), _Device((DOMAIN, "cam0"))))

    def test_a_hub_with_no_cameras_yet_lets_everything_go(self):
        coord, _ = harness._build()
        coord.cameras = []
        self.assertTrue(_may_remove(_hass(coord), _Device((DOMAIN, "cam0"))))


class TheHookIsFound(unittest.TestCase):
    """Home Assistant looks it up by name on the component module. A typo
    means the delete button silently never appears."""

    def test_it_is_named_exactly_what_home_assistant_looks_for(self):
        self.assertTrue(callable(
            getattr(component, "async_remove_config_entry_device", None)))

    def test_it_is_a_coroutine(self):
        import inspect
        self.assertTrue(inspect.iscoroutinefunction(
            component.async_remove_config_entry_device))


if __name__ == "__main__":
    unittest.main()
