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

    def test_no_other_failure_gives_up_on_the_entry(self):
        """Only a named credential refusal may stop the retries. Anything
        else asking for a password puts a check-your-password notice in front
        of somebody whose password is fine, and the hub never gets the retry
        that would have worked."""
        for error in (OSError("no route"), TimeoutError("no response"),
                      ValueError("garbage body"), RuntimeError("hub busy")):
            with self.subTest(error=type(error).__name__):
                class _Failing(_Client):
                    instances: list = []

                    def connect(self):
                        self.connects += 1
                        raise error

                component.H500Client = _Failing
                with self.assertRaises(ConfigEntryNotReady):
                    self._setup()
                self.assertEqual(_Failing.instances[0].closes, 1,
                                 "and the login it opened is closed")

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


class VoiceIsABonus(_World):
    """Assist may not be installed, its intent API has changed shape before,
    and neither is a reason a doorbell stops working."""

    def test_intents_that_will_not_register_do_not_fail_setup(self):
        intent_mod = sys.modules["tapo_h500.intent"]
        original = intent_mod.async_setup_intents

        async def refuse(hass):
            raise RuntimeError("no intent component here")

        intent_mod.async_setup_intents = refuse
        self.addCleanup(setattr, intent_mod, "async_setup_intents", original)
        self.assertTrue(self._setup())
        self.assertIn("test", self.hass.data["tapo_h500"]["hubs"],
                      "the hub is set up regardless")


class Unload(_World):
    def test_platforms_that_will_not_unload_stop_the_unload(self):
        """Closing the login under entities still holding it would strand
        them, and Home Assistant retries the unload afterwards."""
        self._setup()
        async def refuse(entry, platforms):
            return False
        self.hass.config_entries.async_unload_platforms = refuse
        self.assertFalse(run(component.async_unload_entry(
            self.hass, self.entry)))
        self.assertEqual(_Client.instances[0].closes, 0,
                         "the login stays open while entities still hold it")
        self.assertIn(self.entry.entry_id,
                      self.hass.data["tapo_h500"]["hubs"])

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


class TheDashboardCard(unittest.TestCase):
    """Registering the card reaches into Lovelace's own storage, whose shape
    differs across Home Assistant versions -- so what happens when it does
    not look the way this code expects is the part that matters."""

    def setUp(self):
        self.init = component
        self.extra_js = []
        # Both are imported into __init__'s own namespace, so that binding is
        # the one the code reads.
        self._patch(self.init, "add_extra_js_url",
                    lambda hass, url: self.extra_js.append(url))

        async def integration(hass, domain):
            return type("I", (), {"version": "1.4.0"})()

        self._patch(self.init, "async_get_integration", integration)

    def _patch(self, module, name, value):
        original = getattr(module, name, None)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def _hass(self, resources=None):
        hass = harness._Hass()
        hass.data = {}
        served = []

        async def register(paths):
            served.extend(paths)

        hass.http = type("H", (), {
            "async_register_static_paths": staticmethod(register)})()
        if resources is not None:
            hass.data["lovelace"] = type("L", (), {"resources": resources})()
        hass.served = served
        return hass

    class _Resources:
        """Lovelace's storage collection, as much of it as this uses."""

        def __init__(self, items=(), loaded=True, fails=None):
            self.items = [dict(item) for item in items]
            self.loaded = loaded
            self.fails = fails
            self.loads = 0

        def async_items(self):
            if self.fails:
                raise self.fails
            return list(self.items)

        async def async_load(self):
            self.loads += 1

        async def async_create_item(self, item):
            self.items.append({**item, "id": "new"})

        async def async_update_item(self, item_id, changes):
            for item in self.items:
                if item["id"] == item_id:
                    item.update(changes)

    def test_the_card_is_served_and_listed_as_a_resource(self):
        resources = self._Resources()
        hass = self._hass(resources)
        run(self.init._async_register_card(hass))
        self.assertEqual(len(hass.served), 1)
        self.assertEqual(resources.items[0]["res_type"], "module")
        self.assertIn("?v=1.4.0", resources.items[0]["url"])

    def test_the_version_is_what_makes_a_browser_refetch_it(self):
        """A cached copy of the old card is the failure this prevents, and
        it looks like nothing at all going wrong."""
        resources = self._Resources()
        run(self.init._async_register_card(self._hass(resources)))
        self.assertTrue(resources.items[0]["url"].endswith("?v=1.4.0"))

    def test_an_upgrade_rewrites_the_existing_entry_rather_than_adding_one(self):
        resources = self._Resources(
            [{"id": "old", "url": f"{self.init.CARD_URL}?v=1.0.0"}])
        run(self.init._async_register_card(self._hass(resources)))
        self.assertEqual(len(resources.items), 1)
        self.assertIn("?v=1.4.0", resources.items[0]["url"])

    def test_an_entry_already_current_is_left_alone(self):
        resources = self._Resources(
            [{"id": "old", "url": f"{self.init.CARD_URL}?v=1.4.0"}])
        run(self.init._async_register_card(self._hass(resources)))
        self.assertEqual(resources.items[0]["id"], "old")
        self.assertEqual(len(resources.items), 1)

    def test_an_unloaded_resource_list_is_loaded_first(self):
        """Reading it before it has loaded reports no resources, and the card
        would be added a second time on every restart."""
        resources = self._Resources(loaded=False)
        run(self.init._async_register_card(self._hass(resources)))
        self.assertEqual(resources.loads, 1)

    def test_only_one_mechanism_is_used_when_the_resource_took(self):
        """Both, and the file loads twice -- the second define() throws and
        the card is broken."""
        run(self.init._async_register_card(self._hass(self._Resources())))
        self.assertEqual(self.extra_js, [])

    def test_a_lovelace_that_looks_different_falls_back_to_the_js_url(self):
        """YAML mode, or a storage layout this does not recognise. Neither
        is a reason to fail setting the integration up."""
        run(self.init._async_register_card(self._hass(None)))
        self.assertEqual(len(self.extra_js), 1)
        self.assertIn("?v=1.4.0", self.extra_js[0])

    def test_a_resource_list_that_raises_falls_back_too(self):
        resources = self._Resources(fails=RuntimeError("storage moved"))
        run(self.init._async_register_card(self._hass(resources)))
        self.assertEqual(len(self.extra_js), 1)

    def test_it_is_registered_once_however_many_hubs_there_are(self):
        """Two entries would serve the same static path twice, which raises,
        and would add the resource twice."""
        hass = self._hass(self._Resources())
        run(self.init._async_register_card(hass))
        run(self.init._async_register_card(hass))
        self.assertEqual(len(hass.served), 1)
