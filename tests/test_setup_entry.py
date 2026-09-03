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
    made: list = []

    def __init__(self, hass, entry, client):
        self.hass, self.entry, self.client = hass, entry, client
        type(self).made.append(self)

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
        _Coordinator.made = []

    def _setup(self, entry=None):
        """Set an entry up, and register it the way Home Assistant would.

        async_loaded_entries is how everything that needs all the hubs finds
        them, so an entry that never joined config_entries is a hub nothing
        can see -- which is exactly the bug this would otherwise hide.
        """
        entry = entry or self.entry
        if entry not in self.hass.config_entries.entries:
            self.hass.config_entries.entries.append(entry)
        return run(component.async_setup_entry(self.hass, entry))


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
        self.assertEqual(caught.exception.translation_key, "cannot_reach")
        self.assertEqual(caught.exception.translation_placeholders["host"],
                         "192.168.11.5",
                         "the message has to name the hub it could not reach")
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
    def test_the_actions_exist_before_any_hub_is_set_up(self):
        """An action that exists only while an entry is loaded breaks
        automation validation: the hub is unreachable at startup, the entry
        does not load, and every automation calling one fails with "action
        not found"."""
        run(component.async_setup(self.hass, {}))
        self.assertEqual(len(self.hass.services.registered), 13)
        self.assertEqual(self.hass.data.get("tapo_h500", {}).get("hubs"), None)

    def test_calling_one_with_no_hub_says_so_plainly(self):
        """Every handler resolves its own entry, so this is a validation
        error naming the entry rather than a KeyError from inside."""
        from homeassistant.exceptions import ServiceValidationError
        run(component.async_setup(self.hass, {}))
        call = types.SimpleNamespace(data={"config_entry_id": "nothing"})
        for name, handler in self.hass.services.registered.items():
            with self.subTest(action=name):
                with self.assertRaises(ServiceValidationError) as caught:
                    run(handler(call))
                self.assertEqual(caught.exception.translation_key,
                                 "no_hub_for_entry")
                self.assertEqual(
                    caught.exception.translation_placeholders["entry_id"],
                    "nothing")

    def test_the_coordinator_lives_on_the_entry_and_nowhere_else(self):
        """Home Assistant clears runtime_data when the entry unloads, so
        nothing has to remember to -- and no other integration can reach the
        hub by walking hass.data. A parallel copy would quietly undo both."""
        self._setup()
        self.assertIs(self.entry.runtime_data, _Coordinator.made[-1])
        leftover = [key for key in self.hass.data.get("tapo_h500", {})
                    if key == "hubs"]
        self.assertEqual(leftover, [],
                         "the old registry is still being written")

    def test_unloading_lets_go_of_it(self):
        self._setup()
        run(component.async_unload_entry(self.hass, self.entry))
        # Home Assistant clears runtime_data itself; what matters here is
        # that nothing else is still holding the coordinator.
        self.assertEqual(self.hass.data.get("tapo_h500", {}).get("hubs"), None)

    def test_one_login_and_everything_registered(self):
        self.assertTrue(self._setup())
        client = _Client.instances[0]
        self.assertEqual((client.connects, client.closes), (1, 0))
        self.assertIs(self.entry.runtime_data, _Coordinator.made[-1])
        self.assertEqual(len(self.hass.forwarded), 1)
        self.assertEqual(len(self.hass.http.views), 1)
        self.assertEqual(len(self.hass.http.static), 1)
        self.assertEqual(len(self.entry.listeners), 1)
        # The actions are not part of setting an entry up any more; they are
        # registered once at startup and checked in
        # TheActionsBelongToTheIntegration.

    def test_a_second_hub_reuses_the_shared_pieces(self):
        """The card, the view and the services belong to Home Assistant, not
        to a hub; registering them twice makes the card define() throw."""
        self._setup()
        second = _Entry()
        second.entry_id = "second"
        self._setup(second)
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


class ASecondHub(_World):
    """One installation, two hubs. Everything registered once has to stay
    registered once, and everything shared has to survive one of them going.
    """

    def test_the_preview_endpoint_is_served_once(self):
        """Registering the same view twice raises, which fails the second
        hub's setup entirely."""
        self._setup()
        served = len(self.hass.http.views)
        second = _Entry()
        second.entry_id = "second"
        self._setup(entry=second)
        self.assertEqual(len(self.hass.http.views), served)

    def test_each_hub_is_its_own_device(self):
        """Sharing an identifier would put both hubs' entities on one device
        page and make the second one's unavailable when the first unloads."""
        sensor_mod = importlib.import_module("tapo_h500.sensor")
        coord, _ = harness._build()
        first = sensor_mod.hub_device(coord, harness._Entry(20))
        other = harness._Entry(20)
        other.entry_id = "other"
        second = sensor_mod.hub_device(coord, other)
        self.assertNotEqual(first["identifiers"], second["identifiers"])


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
        self.assertIsNotNone(self.entry.runtime_data,
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
        self.assertIsNotNone(
            self.entry.runtime_data,
            "the coordinator stays while entities still hold it")

    def test_the_last_hub_out_closes_its_login(self):
        self._setup()
        self.assertTrue(run(component.async_unload_entry(self.hass,
                                                         self.entry)))
        self.assertEqual(_Client.instances[0].closes, 1)

    def test_but_it_leaves_the_actions_alone(self):
        """They belong to the integration rather than to any one entry. An
        automation calling one while the hub is down should hear "no hub is
        set up", not "action not found" -- which reads as a broken
        automation."""
        run(component.async_setup(self.hass, {}))
        self._setup()
        run(component.async_unload_entry(self.hass, self.entry))
        self.assertEqual(self.hass.services.removed, [])
        self.assertIn("list_recordings", self.hass.services.registered)




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

    def _card_bytes(self):
        from pathlib import Path
        return (Path(self.init.__file__).parent / "www" / "tapo-h500-card.js").read_bytes()

    def test_the_tag_is_what_makes_a_browser_refetch_it(self):
        """A cached copy of the old card is the failure this prevents, and
        it looks like nothing at all going wrong. The version alone only
        changes on a release; the card changes between releases too, so
        the tag follows the file."""
        resources = self._Resources()
        run(self.init._async_register_card(self._hass(resources)))
        expected = self.init.card_version("1.4.0", self._card_bytes())
        self.assertTrue(resources.items[0]["url"].endswith(f"?v={expected}"))

    def test_the_tag_changes_with_the_file_and_only_with_the_file(self):
        same = self.init.card_version("1.4.0", b"card")
        self.assertEqual(same, self.init.card_version("1.4.0", b"card"))
        self.assertNotEqual(same, self.init.card_version("1.4.0", b"card v2"))
        self.assertTrue(same.startswith("1.4.0-"), "the version stays readable")

    def test_an_upgrade_rewrites_the_existing_entry_rather_than_adding_one(self):
        resources = self._Resources(
            [{"id": "old", "url": f"{self.init.CARD_URL}?v=1.0.0"}])
        run(self.init._async_register_card(self._hass(resources)))
        self.assertEqual(len(resources.items), 1)
        self.assertIn("?v=1.4.0", resources.items[0]["url"])

    def test_an_entry_already_current_is_left_alone(self):
        current = f"{self.init.CARD_URL}?v={self.init.card_version('1.4.0', self._card_bytes())}"
        resources = self._Resources([{"id": "old", "url": current}])
        run(self.init._async_register_card(self._hass(resources)))
        self.assertEqual(resources.items, [{"id": "old", "url": current}])

    def test_a_changed_card_under_the_same_version_is_refetched(self):
        """The case that hid for a whole afternoon: same integration version,
        new card file, and every browser kept the old one."""
        stale = f"{self.init.CARD_URL}?v={self.init.card_version('1.4.0', b'the old card')}"
        resources = self._Resources([{"id": "old", "url": stale}])
        run(self.init._async_register_card(self._hass(resources)))
        self.assertEqual(len(resources.items), 1)
        self.assertNotEqual(resources.items[0]["url"], stale)
        self.assertIn("?v=1.4.0-", resources.items[0]["url"])

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

    def test_a_resource_list_shaped_differently_falls_back_too(self):
        """The three shapes a changed storage layout takes."""
        for failure in (AttributeError("no resources"),
                        KeyError("lovelace"),
                        TypeError("not a collection")):
            with self.subTest(failure=type(failure).__name__):
                self.extra_js.clear()
                resources = self._Resources(fails=failure)
                run(self.init._async_register_card(self._hass(resources)))
                self.assertEqual(len(self.extra_js), 1)

    def test_an_unexpected_failure_is_not_swallowed(self):
        """A broad catch here would hide a real bug in the resource write
        behind a warning about the dashboard."""
        resources = self._Resources(fails=RuntimeError("a real bug"))
        with self.assertRaises(RuntimeError):
            run(self.init._async_register_card(self._hass(resources)))

    def test_the_fallback_says_so_where_somebody_will_see_it(self):
        """The warning went to the log, where nobody reads it, and the only
        other symptom is a card claiming its custom element does not exist."""
        raised = []
        self._patch(self.init, "card_not_registered",
                    lambda hass, url: raised.append(url))
        run(self.init._async_register_card(self._hass(None)))
        self.assertEqual(len(raised), 1)
        self.assertIn("?v=", raised[0], "the notice carries the real URL")

    def test_and_clears_it_once_the_card_registers(self):
        """A repair that never clears is worse than one that never appears --
        and this one is cured by a Home Assistant upgrade as often as by
        anything the owner does."""
        cleared = []
        self._patch(self.init, "card_registered", lambda hass: cleared.append(1))
        run(self.init._async_register_card(self._hass(self._Resources())))
        self.assertEqual(len(cleared), 1)

    def test_it_is_registered_once_however_many_hubs_there_are(self):
        """Two entries would serve the same static path twice, which raises,
        and would add the resource twice."""
        hass = self._hass(self._Resources())
        run(self.init._async_register_card(hass))
        run(self.init._async_register_card(hass))
        self.assertEqual(len(hass.served), 1)


if __name__ == "__main__":
    unittest.main()
