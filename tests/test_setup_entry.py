"""Setting an entry up and tearing it down, driven.

__init__.py was the least-covered module left: 31%, and it holds the login
discipline this hub depends on -- every failure path must close the client it
opened, because Home Assistant retries setup on a backoff and each retry is
another login to a device that wedges under repeated ones and recovers only
on a timeout.
"""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import ha_stubs  # noqa: E402
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

from homeassistant.exceptions import (  # noqa: E402
    ConfigEntryAuthFailed, ConfigEntryNotReady,
)

component = ha_stubs.real_module("init")
api = importlib.import_module("tapo_h500.api")


def run(coro):
    return asyncio.run(coro)


class _Client:
    """Counts logins and logouts, which is the whole point here."""

    instances: list = []

    def __init__(self, host, username, password, cloud):
        self.host = host
        self.connects = 0
        self.closes = 0
        self.fail_connect = None
        type(self).instances.append(self)

    def connect(self):
        self.connects += 1
        if self.fail_connect is not None:
            raise self.fail_connect

    def close(self):
        self.closes += 1


class _Coordinator:
    """Stands in for the real one; setup only starts it and stores it."""

    fail_refresh = None

    def __init__(self, hass, entry, client):
        self.hass, self.entry, self.client = hass, entry, client

    async def async_config_entry_first_refresh(self):
        if type(self).fail_refresh is not None:
            raise type(self).fail_refresh

    def async_update_listeners(self):
        self.redraws = getattr(self, "redraws", 0) + 1


class _Entry(harness._Entry):
    def __init__(self, **kwargs):
        super().__init__(20, **kwargs)
        self.data = {"host": "192.168.11.5", "username": "admin",
                     "password": "x", "cloud_password": "y"}
        self.listeners = []
        self.unloaders = []

    def add_update_listener(self, listener):
        self.listeners.append(listener)
        return lambda: None

    def async_on_unload(self, unsub):
        self.unloaders.append(unsub)


class _Http:
    def __init__(self):
        self.views = []
        self.static = []

    def register_view(self, view):
        self.views.append(view)

    async def async_register_static_paths(self, configs):
        self.static.extend(configs)


def _hass():
    hass = harness._Hass()
    hass.http = _Http()
    hass.forwarded = []
    hass.reloads = []

    async def forward(entry, platforms):
        hass.forwarded.append(list(platforms))

    async def unload_platforms(entry, platforms):
        return True

    async def reload(entry_id):
        hass.reloads.append(entry_id)

    hass.config_entries.async_forward_entry_setups = forward
    hass.config_entries.async_unload_platforms = unload_platforms
    hass.config_entries.async_reload = reload
    hass.services.removed = []
    hass.services.async_remove = (
        lambda domain, service: hass.services.removed.append(service))
    return hass


class _World(unittest.TestCase):
    def setUp(self):
        _Client.instances = []
        _Coordinator.fail_refresh = None
        for name, value in (
            ("H500Client", _Client),
            ("H500Coordinator", _Coordinator),
            ("media_root", lambda hass: Path("/media")),
        ):
            self.addCleanup(setattr, component, name,
                            getattr(component, name))
            setattr(component, name, value)

        async def integration(hass, domain):
            return types.SimpleNamespace(version="0.123.0")

        self.addCleanup(setattr, component, "async_get_integration",
                        component.async_get_integration)
        component.async_get_integration = integration
        self.hass = _hass()
        self.entry = _Entry()

    def _setup(self):
        return run(component.async_setup_entry(self.hass, self.entry))


class FailurePaths(_World):
    def test_a_refused_credential_asks_for_a_new_one_and_hangs_up(self):
        """The one failure a retry cannot fix. Retrying is not free here:
        each retry is another login."""
        _FailingAuth.instances = []
        component.H500Client = _FailingAuth
        with self.assertRaises(ConfigEntryAuthFailed):
            self._setup()
        client = _FailingAuth.instances[0]
        self.assertEqual(client.closes, 1)

    def test_anything_else_schedules_a_retry_and_hangs_up(self):
        component.H500Client = _FailingConnect
        _FailingConnect.instances = []
        with self.assertRaises(ConfigEntryNotReady) as caught:
            self._setup()
        self.assertIn("192.168.11.5", str(caught.exception))
        self.assertEqual(_FailingConnect.instances[0].closes, 1)

    def test_a_failed_first_refresh_still_hangs_up(self):
        """Without this, each backoff retry adds another login that is never
        closed."""
        _Coordinator.fail_refresh = OSError("hub busy")
        with self.assertRaises(OSError):
            self._setup()
        self.assertEqual(_Client.instances[0].closes, 1)


class _FailingAuth(_Client):
    instances: list = []

    def connect(self):
        self.connects += 1
        raise api.H500AuthError("refused")


class _FailingConnect(_Client):
    instances: list = []

    def connect(self):
        self.connects += 1
        raise OSError("no route")


class TheHappyPath(_World):
    def test_one_login_and_everything_registered(self):
        self.assertTrue(self._setup())
        client = _Client.instances[0]
        self.assertEqual((client.connects, client.closes), (1, 0))
        self.assertIn("test", self.hass.data["tapo_h500"]["hubs"])
        self.assertEqual(len(self.hass.forwarded), 1)
        self.assertEqual(len(self.hass.http.views), 1)
        self.assertEqual(len(self.hass.http.static), 1)
        self.assertIn("list_recordings", self.hass.services.registered)
        self.assertEqual(len(self.entry.listeners), 1)

    def test_a_second_hub_reuses_the_shared_pieces(self):
        """The card, the view and the services belong to Home Assistant, not
        to a hub; registering them twice makes the card define() throw."""
        self._setup()
        second = _Entry()
        second.entry_id = "second"
        run(component.async_setup_entry(self.hass, second))
        self.assertEqual(len(self.hass.http.views), 1)
        self.assertEqual(len(self.hass.http.static), 1)


class OptionsChanges(_World):
    def _changed(self):
        run(component._async_options_changed(self.hass, self.entry))

    def test_naming_a_face_redraws_without_a_reload(self):
        """A reload tears the coordinator down while the card that asked for
        the name is still using it, and opens a fresh login."""
        self._setup()
        self.entry.options = {**self.entry.options,
                              "face_names": {"7": "Sam"}}
        self._changed()
        self.assertEqual(self.hass.reloads, [])

    def test_a_connection_change_still_reloads(self):
        self._setup()
        self.entry.options = {**self.entry.options, "poll_interval": 30}
        self._changed()
        self.assertEqual(self.hass.reloads, ["test"])


class TheResourceList(_World):
    def _resources(self, items, loaded=True):
        store = types.SimpleNamespace(items=list(items), loaded=loaded)
        store.async_items = lambda: list(store.items)

        async def create(item):
            store.items.append({"id": "new", **item})

        async def update(item_id, changes):
            store.updated = (item_id, changes)

        async def load():
            store.loads = getattr(store, "loads", 0) + 1

        store.async_create_item = create
        store.async_update_item = update
        store.async_load = load
        self.hass.data["lovelace"] = types.SimpleNamespace(resources=store)
        return store

    def _register(self, url="/tapo_h500_static/tapo-h500-card.js?v=1"):
        return run(component._async_register_lovelace_resource(
            self.hass, url))

    def test_a_fresh_install_creates_the_resource(self):
        store = self._resources([])
        self.assertTrue(self._register())
        self.assertEqual(store.items[0]["res_type"], "module")

    def test_an_upgrade_rewrites_the_versioned_url_in_place(self):
        """A browser holding the old card keeps it forever unless the URL
        moves."""
        store = self._resources([
            {"id": "r1", "url": "/tapo_h500_static/tapo-h500-card.js?v=0"}])
        self.assertTrue(self._register("/tapo_h500_static/tapo-h500-card.js?v=1"))
        self.assertEqual(store.updated[0], "r1")

    def test_the_same_version_is_left_alone(self):
        store = self._resources([
            {"id": "r1", "url": "/tapo_h500_static/tapo-h500-card.js?v=1"}])
        self.assertTrue(self._register("/tapo_h500_static/tapo-h500-card.js?v=1"))
        self.assertFalse(hasattr(store, "updated"))

    def test_yaml_mode_gets_the_fallback_not_a_crash(self):
        """YAML mode owns its own resource list; write to it and setup
        dies."""
        self.hass.data.pop("lovelace", None)
        self.assertFalse(self._register())


class Unload(_World):
    def test_the_last_hub_out_turns_the_lights_off(self):
        self._setup()
        self.assertTrue(run(component.async_unload_entry(self.hass,
                                                         self.entry)))
        self.assertEqual(_Client.instances[0].closes, 1)
        self.assertIn("list_recordings", self.hass.services.removed)

    def test_services_survive_while_another_hub_remains(self):
        self._setup()
        second = _Entry()
        second.entry_id = "second"
        run(component.async_setup_entry(self.hass, second))
        run(component.async_unload_entry(self.hass, self.entry))
        self.assertEqual(self.hass.services.removed, [])


if __name__ == "__main__":
    unittest.main()
