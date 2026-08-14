"""Muting notifications without disabling the automation.

The alternative people actually use is turning the automation off, which is a
thing they forget to turn back on. The important properties here are that the
snooze expires by itself, that it never survives a restart, and that it stops
notifications rather than recording -- footage taken during a snooze is the
footage most likely to be wanted afterwards.
"""
import importlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "tapo_h500"
SWITCH = (COMPONENT / "switch.py").read_text()
INIT = (COMPONENT / "__init__.py").read_text()
COORDINATOR = (COMPONENT / "coordinator.py").read_text()
BLUEPRINT = (ROOT / "blueprints" / "automation" / "tapo_h500"
             / "notify_on_detection.yaml").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
dt_stub = sys.modules["homeassistant.util.dt"]

NOW = 1_786_600_000


class _Clock:
    """A movable stand-in for the wall clock, so expiry can be tested without
    waiting for it."""

    def __init__(self, at=NOW):
        self.at = at

    def timestamp(self):
        return self.at


class _Client:
    def cameras(self):
        return [{"device_id": "cam0", "alias": "Front"}]

    def recent(self, camera, start, end):
        return []

    def detections(self, camera, start, end):
        return []

    def hub_status(self):
        return {}


class Snoozing(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.original = dt_stub.utcnow
        dt_stub.utcnow = lambda: self.clock
        self.coord = coordinator_mod.H500Coordinator(
            harness._Hass(), harness._Entry(20), _Client())
        self.coord.async_update_listeners = lambda: None

    def tearDown(self):
        dt_stub.utcnow = self.original

    def test_nothing_is_snoozed_to_begin_with(self):
        self.assertFalse(self.coord.snoozed)

    def test_a_duration_mutes_for_that_long(self):
        self.coord.snooze(3600)
        self.assertTrue(self.coord.snoozed)

    def test_and_stops_when_it_runs_out(self):
        """No timer does this -- every entity reading it redraws on the poll,
        which happens every couple of seconds."""
        self.coord.snooze(3600)
        self.clock.at = NOW + 3601
        self.assertFalse(self.coord.snoozed)

    def test_it_is_still_muted_a_second_before(self):
        self.coord.snooze(3600)
        self.clock.at = NOW + 3599
        self.assertTrue(self.coord.snoozed)

    def test_expiry_clears_the_end_time(self):
        """Or the attribute would keep showing a time in the past, which
        reads as still snoozed."""
        self.coord.snooze(60)
        self.clock.at = NOW + 61
        self.coord.snoozed
        self.assertIsNone(self.coord.snoozed_until)

    def test_no_duration_means_indefinitely(self):
        self.coord.snooze(None)
        self.clock.at = NOW + 10 ** 6
        self.assertTrue(self.coord.snoozed)

    def test_zero_cancels(self):
        self.coord.snooze(3600)
        self.coord.snooze(0)
        self.assertFalse(self.coord.snoozed)

    def test_cancelling_clears_the_end_time_rather_than_setting_it_to_now(self):
        """Both read as "not snoozed", but the end time is what the action
        returns and what the switch shows, and "snoozed until 14:03" is a
        strange way to report a cancellation."""
        self.coord.snooze(3600)
        self.coord.snooze(0)
        self.assertIsNone(self.coord.snoozed_until)

    def test_a_second_snooze_replaces_the_first(self):
        self.coord.snooze(3600)
        self.coord.snooze(60)
        self.clock.at = NOW + 61
        self.assertFalse(self.coord.snoozed)


class NotPersisted(unittest.TestCase):
    def test_the_snooze_is_not_written_to_the_config_entry(self):
        """A snooze that outlived a restart would be a silent doorbell nobody
        remembered turning off."""
        body = COORDINATOR.split("    def snooze(", 1)[1].split(
            "\n    def ", 1)[0]
        self.assertNotIn("async_update_entry", body)
        self.assertNotIn("options", body)


class DoesNotStopRecording(unittest.TestCase):
    def test_the_poll_never_consults_the_snooze(self):
        """Footage taken during a snooze is the footage most likely to be
        wanted afterwards. This mutes the automation, not the hub."""
        poll = COORDINATOR.split("    async def _poll(", 1)[1].split(
            "\n    def _fresh(", 1)[0]
        self.assertNotIn("snooz", poll)

    def test_neither_does_firing_or_downloading(self):
        for name in ("    def _fire(", "    def _download_new("):
            body = COORDINATOR.split(name, 1)[1].split("\n    def ", 1)[0]
            self.assertNotIn("snooz", body)


class Entity(unittest.TestCase):
    def test_the_hub_gets_a_snooze_switch(self):
        setup = SWITCH.split("async_setup_entry", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("H500Snooze(coordinator, entry)", setup)

    def test_turning_it_on_by_hand_is_indefinite(self):
        """switch.turn_on carries nowhere to put a duration."""
        # Scoped to the class: H500HubSwitch declares async_turn_on too, and
        # splitting on the method name alone finds that one.
        body = SWITCH.split("class H500Snooze", 1)[1]
        body = body.split("    async def async_turn_on", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("self.coordinator.snooze(None)", body)

    def test_turning_it_off_cancels(self):
        body = SWITCH.split("class H500Snooze", 1)[1]
        body = body.split("    async def async_turn_off", 1)[1]
        self.assertIn("self.coordinator.snooze(0)", body)


class Action(unittest.TestCase):
    def test_the_service_is_registered(self):
        self.assertIn("(SERVICE_SNOOZE, snooze, SNOOZE_SCHEMA)", INIT)

    def test_minutes_are_optional(self):
        schema = INIT.split("SNOOZE_SCHEMA = vol.Schema(", 1)[1].split(
            "})", 1)[0]
        self.assertIn('vol.Optional("minutes")', schema)

    def test_minutes_are_converted_to_seconds(self):
        body = INIT.split("    async def snooze(", 1)[1].split(
            "\n    for service", 1)[0]
        self.assertIn("minutes * 60", body)

    def test_it_is_described_for_the_ui(self):
        actions = (COMPONENT / "services.yaml").read_text()
        self.assertIn("\nsnooze:", actions)

    def test_the_service_appears_in_the_removal_list(self):
        """Services are removed when the last hub unloads; one missing from
        that tuple lingers and fails when called."""
        removal = INIT.split("SERVICES = (", 1)[1].split(")", 1)[0]
        self.assertIn("SERVICE_SNOOZE", removal)


class Blueprint(unittest.TestCase):
    def test_it_offers_a_snooze_input(self):
        self.assertIn("snooze_entity:", BLUEPRINT)

    def test_the_input_is_read_by_a_condition(self):
        self.assertIn("input_snooze_entity", BLUEPRINT)
        conditions = BLUEPRINT.split("conditions:", 1)[1].split(
            "\nvariables:", 1)[0]
        self.assertIn("input_snooze_entity", conditions)

    def test_an_empty_snooze_entity_notifies_as_before(self):
        """Every other input defaults to off; this one must too, or adding it
        silences existing automations on upgrade."""
        conditions = BLUEPRINT.split("conditions:", 1)[1].split(
            "\nvariables:", 1)[0]
        self.assertRegex(conditions, r"not input_snooze_entity\s*\n?\s*or ")

    def test_the_input_defaults_to_nothing(self):
        block = BLUEPRINT.split("    snooze_entity:", 1)[1].split(
            "\n    repeat_minutes:", 1)[0]
        self.assertRegex(block, r'default:\s*""')


class Removal(unittest.TestCase):
    def test_every_registered_service_can_be_removed(self):
        """The removal tuple and the registration list drifted apart once
        already; a service in one and not the other never goes away."""
        registered = set(re.findall(r"\(SERVICE_(\w+), \w+, \w+_SCHEMA\)", INIT))
        listed = set(re.findall(r"SERVICE_(\w+)",
                                INIT.split("SERVICES = (", 1)[1]
                                .split("\n)", 1)[0]))
        self.assertEqual(registered - listed, set())


if __name__ == "__main__":
    unittest.main()
