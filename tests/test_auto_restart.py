"""Opt-in self-healing for the one failure a restart provably cures.

Both media failure modes -- the refused-session wedge and the hollow
sessions measured on 2026-08-18 -- are cured by a hub reboot and by
nothing else that has ever been found. With the option ON, the
coordinator presses its own restart button when either state is seen,
under guards that make a reboot loop impossible: a six-hour cooldown, a
loud warning, and a bus event an automation can notify on.

OFF by default, and off means off: the standing rule that nothing in this
integration reboots the hub on its own becomes "...unless the owner turned
exactly that on".
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")


def _build(auto=True, interval=450):
    client = harness._Client()
    client.reboots = 0
    client.check_media = lambda: "healthy"

    def reboot():
        client.reboots += 1

    client.reboot = reboot
    entry = harness._Entry(interval, **({"auto_restart": True} if auto else {}))
    coord = coordinator_mod.H500Coordinator(harness._Hass(), entry, client)
    coord._download_new = lambda *a, **k: None
    return coord, client


def _poll(coord, times=1):
    for _ in range(times):
        asyncio.run(coord._async_update_data())


class AutoRestart(unittest.TestCase):
    def test_off_by_default_means_never(self):
        coord, client = _build(auto=False)
        coord.note_empty_download()
        coord.note_empty_download()
        _poll(coord, 4)
        self.assertEqual(client.reboots, 0)

    def test_hollow_sessions_trigger_one_restart(self):
        coord, client = _build()
        coord.note_empty_download()
        coord.note_empty_download()
        _poll(coord)
        self.assertEqual(client.reboots, 1)

    def test_the_wedge_triggers_one_restart(self):
        coord, client = _build()
        client.check_media = lambda: "wedged"
        _poll(coord)
        self.assertEqual(client.reboots, 1)

    def test_the_cooldown_makes_a_reboot_loop_impossible(self):
        """The state persists through the reboot window (the hub is down,
        checks keep saying wedged) -- and still only one restart happens
        until the cooldown passes."""
        coord, client = _build()
        client.check_media = lambda: "wedged"
        _poll(coord, 6)
        self.assertEqual(client.reboots, 1)

    def test_the_empty_counter_is_reset_so_recovery_can_be_seen(self):
        coord, client = _build()
        coord.note_empty_download()
        coord.note_empty_download()
        _poll(coord)
        self.assertFalse(coord.media_serving_empty)

    def test_it_says_so_where_an_automation_can_hear(self):
        coord, client = _build()
        coord.note_empty_download()
        coord.note_empty_download()
        _poll(coord)
        fired = [event for event, _ in coord.hass.bus.fired
                 if event == const.EVENT_AUTO_RESTART]
        self.assertEqual(len(fired), 1)
        _, data = coord.hass.bus.fired[0]
        self.assertIn(data.get("reason"), ("wedged", "empty"))

    def test_a_healthy_hub_is_left_alone(self):
        coord, client = _build()
        _poll(coord, 4)
        self.assertEqual(client.reboots, 0)

    def test_a_failed_restart_does_not_fail_the_poll(self):
        coord, client = _build()

        def boom():
            raise OSError("connection dropped")  # what success looks like

        client.reboot = boom
        coord.note_empty_download()
        coord.note_empty_download()
        _poll(coord)  # must not raise

    def test_the_option_is_on_the_settings_form(self):
        flow = (COMPONENT / "config_flow.py").read_text()
        self.assertIn("CONF_AUTO_RESTART", flow.split("async_step_settings", 1)[1])

    def test_changing_it_costs_no_login(self):
        self.assertNotIn(const.CONF_AUTO_RESTART, const.RELOAD_ON_CHANGE)


if __name__ == "__main__":
    unittest.main()
