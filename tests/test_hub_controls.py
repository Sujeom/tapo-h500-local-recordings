"""The writable hub settings, driven as entities rather than read as text.

switch, number, select and siren share one contract, kept in hub_control: run
the blocking client call off the event loop, turn a hub refusal into an error
Home Assistant can show, and poll exactly once afterwards so the new value is
read back rather than guessed. Five of these modules had never been
constructed by any test -- 271 lines at 0.0% coverage, on the surface that
writes to a device that is easy to overload.

Every rule these tests hold was verified against an H500 on firmware 1.3.20
before it was code: volume outside 1-10 is refused with -40209, the
auto-upgrade and face-detection setters replace their whole block, and a
toggle that sends half a block wipes the half it left out.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

from homeassistant.exceptions import HomeAssistantError  # noqa: E402

hub_control = importlib.import_module("tapo_h500.hub_control")
switch_mod = importlib.import_module("tapo_h500.switch")
number_mod = importlib.import_module("tapo_h500.number")
select_mod = importlib.import_module("tapo_h500.select")
siren_mod = importlib.import_module("tapo_h500.siren")


class _Client(harness._Client):
    """Records every setter call; refuses them all when told to."""

    def __init__(self):
        super().__init__()
        self.writes: list[tuple] = []
        self.refuse = None
        self.tones = ["Alarm 1", "Doorbell"]

    def _write(self, *record):
        if self.refuse is not None:
            raise self.refuse
        self.writes.append(record)

    def set_led(self, on): self._write("led", on)
    def set_loop_recording(self, on): self._write("loop", on)
    def set_diagnose_mode(self, on): self._write("diagnose", on)
    def set_face_detection(self, detection): self._write("face", detection)
    def set_auto_upgrade(self, config): self._write("upgrade", config)
    def set_siren(self, on): self._write("siren", on)

    def set_siren_config(self, tone=None, volume=None, duration=None):
        self._write("siren_config",
                    {"tone": tone, "volume": volume, "duration": duration})

    def siren_tones(self):
        if isinstance(self.tones, Exception):
            raise self.tones
        return list(self.tones)


def _coordinator(**readings):
    coord, _ = harness._build()
    coord.client = _Client()
    coord.readings = readings
    coord.refreshes = 0
    real = coord.async_refresh_after_write

    async def counted():
        coord.refreshes += 1

    coord.async_refresh_after_write = counted
    del real
    return coord


def _hass():
    return harness._Hass()


def run(coro):
    return asyncio.run(coro)


class TheSharedContract(unittest.TestCase):
    """What every control owes: one refresh on success, a shown error and no
    refresh on refusal."""

    def _switch(self, **readings):
        coord = _coordinator(**readings)
        entity = switch_mod.H500HubSwitch(
            coord, harness._Entry(20), switch_mod.HUB_SWITCHES[0])
        entity.hass = _hass()
        return entity, coord

    def test_a_write_reads_the_result_back_exactly_once(self):
        """Guessed state on a hub that answers slowly is how a switch shows
        on while the LED is off. One refresh, not one per call -- the hub is
        easy to overload."""
        entity, coord = self._switch(led_on=False)
        run(entity.async_turn_on())
        self.assertEqual(coord.refreshes, 1)

    def test_a_refusal_is_an_error_home_assistant_can_show(self):
        entity, coord = self._switch(led_on=False)
        coord.client.refuse = OSError("-40209")
        with self.assertRaises(HomeAssistantError) as caught:
            run(entity.async_turn_on())
        self.assertIn("refused", str(caught.exception))

    def test_a_refused_write_does_not_refresh(self):
        """The poll after a write exists to read the new value. After a
        refusal there is no new value, and the extra round trip is exactly
        the traffic this hub wedges under."""
        entity, coord = self._switch(led_on=False)
        coord.client.refuse = OSError("-40209")
        with self.assertRaises(HomeAssistantError):
            run(entity.async_turn_on())
        self.assertEqual(coord.refreshes, 0)


class TheSwitches(unittest.TestCase):
    def _switch(self, key, **readings):
        coord = _coordinator(**readings)
        description = next(d for d in switch_mod.HUB_SWITCHES if d.key == key)
        entity = switch_mod.H500HubSwitch(coord, harness._Entry(20), description)
        entity.hass = _hass()
        return entity, coord

    def test_each_reads_its_own_reading(self):
        for key, reading in (("led", "led_on"),
                             ("loop_recording", "loop_recording"),
                             ("auto_upgrade", "auto_upgrade"),
                             ("face_detection", "face_detection"),
                             ("diagnose_mode", "diagnose_mode")):
            with self.subTest(key):
                entity, _ = self._switch(key, **{reading: True})
                self.assertIs(entity.is_on, True)

    def test_a_reading_the_hub_did_not_send_is_unknown_not_off(self):
        entity, _ = self._switch("led")
        self.assertIsNone(entity.is_on)

    def test_the_led_switch_writes_the_led(self):
        entity, coord = self._switch("led", led_on=False)
        run(entity.async_turn_on())
        run(entity.async_turn_off())
        self.assertEqual(coord.client.writes, [("led", True), ("led", False)])

    def test_the_upgrade_toggle_sends_the_schedule_back(self):
        """setFirmwareAutoUpgradeConfig replaces the whole block. A toggle
        that sends only `enabled` silently wipes the update window."""
        entity, coord = self._switch(
            "auto_upgrade", auto_upgrade=True,
            auto_upgrade_config={"enabled": "on", "time": "03:00",
                                 "random_range": 120})
        run(entity.async_turn_off())
        kind, sent = coord.client.writes[0]
        self.assertEqual(kind, "upgrade")
        self.assertEqual(sent, {"enabled": "off", "time": "03:00",
                                "random_range": 120})

    def test_the_toggle_copies_rather_than_mutates_the_readings(self):
        """The config block is the coordinator's live readings dict."""
        stored = {"enabled": "on", "time": "03:00"}
        entity, coord = self._switch("auto_upgrade", auto_upgrade=True,
                                     auto_upgrade_config=stored)
        run(entity.async_turn_off())
        self.assertEqual(stored["enabled"], "on")

    def test_the_face_toggle_sends_the_tags_back(self):
        """Same trap: `enabled` alone is refused with -40211."""
        entity, coord = self._switch(
            "face_detection", face_detection=False,
            face_detection_tags=["stranger", "acquaintance"])
        run(entity.async_turn_on())
        kind, sent = coord.client.writes[0]
        self.assertEqual(kind, "face")
        self.assertEqual(sent, {"enabled": "on",
                                "tags": ["stranger", "acquaintance"]})

    def test_every_switch_is_registered(self):
        added = []
        coord = _coordinator()
        hass = _hass()
        coord.entry.runtime_data = coord

        hass.config_entries = harness._ConfigEntries([coord.entry])
        run(switch_mod.async_setup_entry(hass, coord.entry, added.extend))
        self.assertEqual(len(added), 6, "five settings and the snooze")


class TheSnoozeSwitch(unittest.TestCase):
    def _snooze(self):
        coord = _coordinator()
        entity = switch_mod.H500Snooze(coord, harness._Entry(20))
        entity.hass = _hass()
        return entity, coord

    def test_flipping_it_on_mutes_indefinitely(self):
        """switch.turn_on carries nowhere to put a duration; the snooze
        action is where an hour goes."""
        entity, coord = self._snooze()
        run(entity.async_turn_on())
        self.assertTrue(coord.snoozed)
        self.assertEqual(coord.snoozed_until, float("inf"))

    def test_flipping_it_off_unmutes(self):
        entity, coord = self._snooze()
        run(entity.async_turn_on())
        run(entity.async_turn_off())
        self.assertFalse(coord.snoozed)

    def test_indefinite_shows_no_until(self):
        """An infinity rendered as a year-292277026596 timestamp is worse
        than nothing."""
        entity, coord = self._snooze()
        run(entity.async_turn_on())
        self.assertIsNone(entity.extra_state_attributes["until"])

    def test_a_timed_snooze_shows_when_it_ends(self):
        entity, coord = self._snooze()
        coord.snooze(3600)
        self.assertTrue(
            entity.extra_state_attributes["until"].startswith("20"))


class TheNumbers(unittest.TestCase):
    def _number(self, key, **readings):
        coord = _coordinator(**readings)
        description = next(d for d in number_mod.HUB_NUMBERS if d.key == key)
        entity = number_mod.H500HubNumber(coord, harness._Entry(20), description)
        entity.hass = _hass()
        return entity, coord

    def test_volume_reads_and_writes_the_hubs_own_scale(self):
        entity, coord = self._number("siren_volume", siren_volume=7)
        self.assertEqual(entity.native_value, 7)
        run(entity.async_set_native_value(4.0))
        self.assertEqual(coord.client.writes, [
            ("siren_config", {"tone": None, "volume": 4, "duration": None})])

    def test_duration_writes_seconds(self):
        entity, coord = self._number("siren_duration", siren_duration=300)
        run(entity.async_set_native_value(120.0))
        self.assertEqual(coord.client.writes, [
            ("siren_config", {"tone": None, "volume": None, "duration": 120})])

    def test_a_fractional_value_reaches_the_hub_as_an_integer(self):
        """The hub's own scale is integers, and a slider can produce 4.6.
        A float in the payload is a -40209 waiting for a firmware to mind."""
        entity, coord = self._number("siren_volume", siren_volume=7)
        run(entity.async_set_native_value(4.6))
        sent = coord.client.writes[0][1]["volume"]
        self.assertEqual(sent, 4)
        self.assertIsInstance(sent, int)

    def test_the_bounds_are_the_hubs_own(self):
        """1-10 measured on hardware: 0 and 11 answer -40209. An hour's cap
        on duration because there is no undo button on a siren."""
        volume = next(d for d in number_mod.HUB_NUMBERS
                      if d.key == "siren_volume")
        duration = next(d for d in number_mod.HUB_NUMBERS
                        if d.key == "siren_duration")
        self.assertEqual((volume.native_min_value, volume.native_max_value),
                         (1, 10))
        self.assertEqual(duration.native_max_value, 3600)


class TheToneSelect(unittest.TestCase):
    def _select(self, tones=("Alarm 1", "Doorbell"), **readings):
        coord = _coordinator(**readings)
        entity = select_mod.H500SirenTone(coord, harness._Entry(20),
                                          list(tones))
        entity.hass = _hass()
        return entity, coord

    def test_it_offers_the_hubs_own_list(self):
        entity, _ = self._select(siren_tone="Doorbell")
        self.assertEqual(entity.options, ["Alarm 1", "Doorbell"])
        self.assertEqual(entity.current_option, "Doorbell")

    def test_a_tone_outside_the_list_reads_as_unknown(self):
        """Home Assistant logs a warning for a value outside the options; a
        firmware that renames a tone must not fill the log."""
        entity, _ = self._select(siren_tone="Tone the hub renamed")
        self.assertIsNone(entity.current_option)

    def test_choosing_writes_the_tone(self):
        entity, coord = self._select(siren_tone="Alarm 1")
        run(entity.async_select_option("Doorbell"))
        self.assertEqual(coord.client.writes, [
            ("siren_config",
             {"tone": "Doorbell", "volume": None, "duration": None})])

    def _setup(self, coord):
        added = []
        hass = _hass()
        coord.entry.runtime_data = coord

        hass.config_entries = harness._ConfigEntries([coord.entry])
        run(select_mod.async_setup_entry(hass, coord.entry,
                                         added.extend))
        return added

    def test_no_tone_list_means_no_entity(self):
        """A free text box would just produce -40209s."""
        coord = _coordinator()
        coord.client.tones = []
        self.assertEqual(self._setup(coord), [])

    def test_a_hub_that_will_not_list_tones_does_not_break_setup(self):
        coord = _coordinator()
        coord.client.tones = OSError("hub busy")
        self.assertEqual(self._setup(coord), [])


class TheSiren(unittest.TestCase):
    def _siren(self, tones=("Alarm 1",), **readings):
        coord = _coordinator(**readings)
        entity = siren_mod.H500Siren(coord, harness._Entry(20), list(tones))
        entity.hass = _hass()
        return entity, coord

    def test_it_reports_the_hubs_state_and_settings(self):
        entity, _ = self._siren(siren_active=True, siren_tone="Alarm 1",
                                siren_volume=8, siren_duration=300,
                                siren_time_left=42)
        self.assertIs(entity.is_on, True)
        self.assertEqual(entity.extra_state_attributes, {
            "tone": "Alarm 1", "volume": 8, "duration": 300, "time_left": 42})

    def test_plain_turn_on_is_one_call_and_one_refresh(self):
        entity, coord = self._siren()
        run(entity.async_turn_on())
        self.assertEqual(coord.client.writes, [("siren", True)])
        self.assertEqual(coord.refreshes, 1)

    def test_settings_are_applied_before_the_noise_starts(self):
        """Config is a separate call. Sound first and the opening seconds
        play at the previous volume."""
        entity, coord = self._siren()
        run(entity.async_turn_on(tone="Alarm 1", volume_level=0.5,
                                 duration=60))
        self.assertEqual(coord.client.writes, [
            ("siren_config", {"tone": "Alarm 1", "volume": 5, "duration": 60}),
            ("siren", True),
        ])
        self.assertEqual(coord.refreshes, 1, "still one refresh at the end")

    def test_home_assistants_level_lands_on_the_hubs_scale(self):
        """0.0-1.0 onto 1-10, clamped: the hub refuses 0 and 11 with -40209
        and 0.0 is a level Home Assistant will legitimately send."""
        for level, expected in ((0.0, 1), (0.05, 1), (0.5, 5), (1.0, 10)):
            with self.subTest(level):
                entity, coord = self._siren()
                run(entity.async_turn_on(volume_level=level))
                self.assertEqual(coord.client.writes[0][1]["volume"], expected)

    def test_turn_off_stops_it_and_reads_back(self):
        entity, coord = self._siren()
        run(entity.async_turn_off())
        self.assertEqual(coord.client.writes, [("siren", False)])
        self.assertEqual(coord.refreshes, 1)

    def test_a_refusal_is_shown_not_swallowed(self):
        entity, coord = self._siren()
        coord.client.refuse = OSError("-40209")
        with self.assertRaises(HomeAssistantError):
            run(entity.async_turn_on())
        self.assertEqual(coord.refreshes, 0)

    def test_a_siren_without_a_tone_list_still_switches(self):
        added = []
        coord = _coordinator()
        coord.client.tones = OSError("hub busy")
        hass = _hass()
        coord.entry.runtime_data = coord

        hass.config_entries = harness._ConfigEntries([coord.entry])
        run(siren_mod.async_setup_entry(hass, coord.entry,
                                        added.extend))
        self.assertEqual(len(added), 1)


if __name__ == "__main__":
    unittest.main()
