"""The device triggers, attached and fired rather than read as text.

test_device_trigger.py holds the wiring and the translations statically; this
file drives the module. What a static check cannot see is behaviour: whether
the person trigger actually fires for a person who also tripped motion,
whether a state trigger really asks for "on" only, and whether a bus trigger
is really filtered to its own hub -- the difference between a two-doorbell
house and announcing the neighbours'.
"""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

trigger_mod = importlib.import_module("tapo_h500.device_trigger")
er = sys.modules["homeassistant.helpers.entity_registry"]
dr = sys.modules["homeassistant.helpers.device_registry"]
# Reached through the module under test rather than sys.modules: the finder
# manufactures them as attribute lookups, so the dotted names may never be
# registered -- but these are the exact objects device_trigger will call.
state_trigger = trigger_mod.state_trigger
event_trigger = trigger_mod.event_trigger

HUB_ENTRY = "test"


def run(coro):
    return asyncio.run(coro)


def _entity(domain, unique_id, registry_id):
    return types.SimpleNamespace(domain=domain, unique_id=unique_id,
                                 id=registry_id)


class _Harness:
    """Registries and trigger helpers, recorded rather than performed."""

    def __init__(self, test, entities=(), identifiers=frozenset()):
        self.attached = []
        hass = harness._Hass()
        coord, _ = harness._build()
        hass.data = {"tapo_h500": {"hubs": {HUB_ENTRY: coord}}}
        self.hass = hass

        device = types.SimpleNamespace(identifiers=set(identifiers))
        registry = object()

        patches = [
            (er, "async_get", lambda h: registry),
            (er, "async_entries_for_device",
             lambda reg, device_id: list(entities)),
            (er, "async_validate_entity_id",
             lambda reg, value: f"resolved:{value}"),
            (dr, "async_get",
             lambda h: types.SimpleNamespace(
                 async_get=lambda device_id: device)),
            (state_trigger, "async_validate_trigger_config",
             self._passthrough),
            (state_trigger, "async_attach_trigger", self._attach("state")),
            (event_trigger, "TRIGGER_SCHEMA", lambda config: config),
            (event_trigger, "async_attach_trigger", self._attach("event")),
        ]
        for module, name, value in patches:
            original = getattr(module, name, None)
            setattr(module, name, value)
            if original is not None:
                test.addCleanup(setattr, module, name, original)

    async def _passthrough(self, hass, config):
        return config

    def _attach(self, kind):
        async def attach(hass, config, action, info, platform_type=None):
            self.attached.append((kind, config, action, platform_type))
            return lambda: None

        return attach


class WhatADeviceOffers(unittest.TestCase):
    def _triggers(self, entities=(), identifiers=frozenset()):
        env = _Harness(self, entities, identifiers)
        return run(trigger_mod.async_get_triggers(env.hass, "device-1"))

    def test_a_camera_offers_nine_detections_off_its_event_entity(self):
        offered = self._triggers([_entity("event", "cam0_activity", "reg-e")])
        self.assertEqual(len(offered), 9)
        self.assertEqual({t["type"] for t in offered},
                         set(trigger_mod.TRIGGER_TYPES))
        self.assertTrue(all(t["entity_id"] == "reg-e" for t in offered))

    def test_its_worked_out_sensors_ride_along(self):
        offered = self._triggers([
            _entity("binary_sensor", "cam0_loitering", "reg-l"),
            _entity("binary_sensor", "cam0_possible_delivery", "reg-d"),
        ])
        self.assertEqual({t["type"] for t in offered},
                         {"loitering", "possible_delivery"})

    def test_a_sensor_is_matched_on_its_unique_id_not_its_entity_id(self):
        """The owner can rename an entity to anything; the unique id is
        what survives."""
        offered = self._triggers([
            _entity("binary_sensor", "cam0_silent", "reg-s"),
        ])
        self.assertEqual(offered, [], "silent has no device trigger")

    def test_the_hub_offers_the_two_bus_events_and_no_detections(self):
        offered = self._triggers(
            identifiers={("tapo_h500", HUB_ENTRY)})
        self.assertEqual({t["type"] for t in offered}, {"arrival", "visit"})
        for t in offered:
            self.assertNotIn("entity_id", t,
                             "there is no entity behind a bus event")

    def test_a_camera_is_not_mistaken_for_the_hub(self):
        """Both are identified as (DOMAIN, <something>); being a loaded hub
        is what tells them apart."""
        offered = self._triggers(
            [_entity("event", "cam0_activity", "reg-e")],
            identifiers={("tapo_h500", "cam0-device-id")})
        self.assertNotIn("arrival", {t["type"] for t in offered})


class AttachingADetection(unittest.TestCase):
    def _attach(self, slug="person"):
        env = _Harness(self)
        fired = []
        run(trigger_mod.async_attach_trigger(
            env.hass,
            {"type": slug, "entity_id": "reg-e", "device_id": "device-1"},
            lambda variables, context=None: fired.append(variables),
            None))
        kind, config, action, platform_type = env.attached[0]
        return config, action, platform_type, fired

    @staticmethod
    def _state(codes):
        return {"trigger": {"to_state": types.SimpleNamespace(
            attributes={"detection_types": codes})}}

    def test_it_watches_the_resolved_entity_as_a_device_trigger(self):
        config, _, platform_type, _ = self._attach()
        self.assertEqual(config["entity_id"], "resolved:reg-e")
        self.assertEqual(platform_type, "device")

    def test_a_person_who_also_tripped_motion_still_matches(self):
        """detection_types lists everything at once; comparing the headline
        alarm_type would miss exactly this case."""
        _, action, _, fired = self._attach("person")
        action(self._state([2, 6]))
        self.assertEqual(len(fired), 1)

    def test_plain_motion_does_not_fire_the_person_trigger(self):
        _, action, _, fired = self._attach("person")
        action(self._state([2]))
        self.assertEqual(fired, [])

    def test_a_state_with_no_detections_is_ignored(self):
        _, action, _, fired = self._attach("person")
        action(self._state([]))
        action({"trigger": {"to_state": None}})
        action({"trigger": None})
        self.assertEqual(fired, [])

    def test_every_slug_maps_to_its_own_code(self):
        env = _Harness(self)
        for slug, code in trigger_mod.TRIGGER_TYPES.items():
            with self.subTest(slug):
                fired = []
                run(trigger_mod.async_attach_trigger(
                    env.hass,
                    {"type": slug, "entity_id": "reg-e",
                     "device_id": "device-1"},
                    lambda variables, context=None: fired.append(1), None))
                _, _, action, _ = env.attached[-1]
                action(self._state([code]))
                action(self._state([99]))
                self.assertEqual(len(fired), 1)


class AttachingAState(unittest.TestCase):
    def test_it_fires_on_turning_on_only(self):
        """These sensors clear by themselves; firing again as somebody walks
        away is how an automation gets muted."""
        env = _Harness(self)
        run(trigger_mod.async_attach_trigger(
            env.hass,
            {"type": "loitering", "entity_id": "reg-l", "device_id": "d"},
            lambda variables, context=None: None, None))
        kind, config, _, platform_type = env.attached[0]
        self.assertEqual(kind, "state")
        self.assertEqual(config["to"], "on")
        self.assertEqual(config["entity_id"], "resolved:reg-l")
        self.assertEqual(platform_type, "device")


class AttachingABusEvent(unittest.TestCase):
    def _attach(self, slug, identifiers):
        env = _Harness(self, identifiers=identifiers)
        run(trigger_mod.async_attach_trigger(
            env.hass, {"type": slug, "device_id": "device-1"},
            lambda variables, context=None: None, None))
        return env.attached[0]

    def test_an_arrival_listens_for_its_event_from_this_hub_only(self):
        """Both events are fired by every hub; unfiltered, a two-hub
        installation announces the neighbours' front door."""
        kind, config, _, _ = self._attach(
            "arrival", {("tapo_h500", HUB_ENTRY)})
        self.assertEqual(kind, "event")
        self.assertEqual(config["event_data"], {"entry_id": HUB_ENTRY})

    def test_each_slug_maps_to_its_own_bus_event(self):
        arrival = self._attach("arrival", {("tapo_h500", HUB_ENTRY)})[1]
        visit = self._attach("visit", {("tapo_h500", HUB_ENTRY)})[1]
        self.assertNotEqual(arrival["event_type"], visit["event_type"])

    def test_the_kinds_do_not_leak_into_each_other(self):
        """Detections are the fall-through: a routing slip would look up
        TRIGGER_TYPES["visit"], raise, and the automation would never
        attach."""
        kind, _, _, _ = self._attach("visit", {("tapo_h500", HUB_ENTRY)})
        self.assertEqual(kind, "event")


if __name__ == "__main__":
    unittest.main()


class EverySlugCanBeAttached(unittest.TestCase):
    """The editor lists triggers, the owner picks one, and it is saved into
    their automations file. A slug that can be offered but not attached makes
    an automation that looks right and never fires -- and detections are the
    fall-through, so the failure is silent rather than loud.
    """

    def _attach(self, slug):
        env = _Harness(self, identifiers={("tapo_h500", HUB_ENTRY)})
        run(trigger_mod.async_attach_trigger(
            env.hass, {"type": slug, "entity_id": "reg-x", "device_id": "d"},
            lambda variables, context=None: None, None))
        self.assertEqual(len(env.attached), 1, slug)
        return env.attached[0]

    def test_a_bus_event_slug_attaches_to_the_bus(self):
        for slug in trigger_mod.EVENT_TRIGGERS:
            with self.subTest(slug=slug):
                self.assertEqual(self._attach(slug)[0], "event")

    def test_a_worked_out_state_attaches_watching_for_it_turning_on(self):
        """These sensors clear by themselves; firing again as somebody walks
        away is how an automation gets muted."""
        for slug in trigger_mod.STATE_TRIGGERS:
            with self.subTest(slug=slug):
                kind, config, _, _ = self._attach(slug)
                self.assertEqual(kind, "state")
                self.assertEqual(config.get("to"), "on")

    def test_a_detection_watches_the_entity_without_a_target_state(self):
        """Both go through a state watcher, and the filter is what tells them
        apart: a detection matches on what fired, not on turning on. A slug
        routed to the wrong one would look attached and never match."""
        for slug in trigger_mod.TRIGGER_TYPES:
            with self.subTest(slug=slug):
                kind, config, _, _ = self._attach(slug)
                self.assertEqual(kind, "state")
                self.assertIsNone(config.get("to"))

    def test_the_three_kinds_do_not_overlap(self):
        """A slug in two tables would attach as whichever branch came first,
        and the other table's meaning would silently never apply."""
        tables = (set(trigger_mod.TRIGGER_TYPES),
                  set(trigger_mod.STATE_TRIGGERS),
                  set(trigger_mod.EVENT_TRIGGERS))
        for left in range(len(tables)):
            for right in range(left + 1, len(tables)):
                self.assertEqual(tables[left] & tables[right], set())
