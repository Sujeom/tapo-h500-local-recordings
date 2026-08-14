"""When the hub updates itself, which was parsed and then dropped.

`switch.<hub>_automatic_firmware_updates` says whether it happens. The hour it
happens at came back in the same block, was flattened into `hub_readings`, and
reached nothing -- so the only visible half of the setting was the half that
decides least. A hub that reboots itself to install firmware at three in the
afternoon is worth knowing about before it does.

The block is the one the switch must send back whole on every toggle, so
reading the time from it is also what stops the sensor and the switch
disagreeing about the schedule.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "sensor.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

status = importlib.import_module("tapo_h500.status")


def upgrade(**block):
    return {"getFirmwareAutoUpgradeConfig": {"auto_upgrade": {"common": block}}}


class Reading(unittest.TestCase):
    def test_the_time_is_carried(self):
        readings = status.hub_readings(
            upgrade(enabled="on", time="03:00", random_range="120"))
        self.assertEqual(readings["auto_upgrade_time"], "03:00")

    def test_it_is_kept_when_updates_are_off(self):
        """The hub stores the hour whether or not it is going to use it, and
        showing nothing there would look like a hub with no schedule rather
        than one with updates turned off."""
        readings = status.hub_readings(upgrade(enabled="off", time="03:00"))
        self.assertEqual(readings["auto_upgrade_time"], "03:00")
        self.assertIs(readings["auto_upgrade"], False)

    def test_a_hub_that_does_not_say_reports_nothing(self):
        self.assertIsNone(status.hub_readings({})["auto_upgrade_time"])

    def test_the_whole_block_is_kept_for_the_switch(self):
        """setFirmwareAutoUpgradeConfig replaces `common` wholesale, so a
        toggle has to send back the time and window it is not changing."""
        readings = status.hub_readings(
            upgrade(enabled="on", time="03:00", random_range="120"))
        self.assertEqual(readings["auto_upgrade_config"]["random_range"], "120")

    def test_toggling_does_not_wipe_the_schedule(self):
        """The trap this reading exists to avoid. Sending just `enabled` would
        drop the hour the sensor now shows."""
        readings = status.hub_readings(
            upgrade(enabled="on", time="03:00", random_range="120"))
        rewritten = status.auto_upgrade_config(readings, False)
        self.assertEqual(rewritten["time"], "03:00")
        self.assertEqual(rewritten["random_range"], "120")
        self.assertEqual(rewritten["enabled"], "off")

    def test_rewriting_does_not_mutate_the_live_readings(self):
        """auto_upgrade_config is handed the coordinator's own dictionary."""
        readings = status.hub_readings(upgrade(enabled="on", time="03:00"))
        status.auto_upgrade_config(readings, False)
        self.assertEqual(readings["auto_upgrade_config"]["enabled"], "on")


class Entity(unittest.TestCase):
    def test_the_sensor_exists(self):
        self.assertIn('key="auto_upgrade_time"', SOURCE)

    def test_it_is_diagnostic(self):
        """It is a hub setting, not something about the house."""
        block = SOURCE.split('key="auto_upgrade_time"', 1)[1] \
                      .split("\n    ),", 1)[0]
        self.assertIn("EntityCategory.DIAGNOSTIC", block)

    def test_it_says_whether_the_schedule_is_running(self):
        """A time on its own reads as a schedule that is in force, and it may
        not be."""
        block = SOURCE.split('key="auto_upgrade_time"', 1)[1] \
                      .split("\n    ),", 1)[0]
        self.assertIn('"enabled": r.get("auto_upgrade")', block)

    def test_it_reports_the_window_too(self):
        """The hub spreads updates over a window after the hour, so the hour
        alone is not when it happens. Read from the kept block rather than
        named and left empty, which is a key that looks answered."""
        block = SOURCE.split('key="auto_upgrade_time"', 1)[1] \
                      .split("\n    ),", 1)[0]
        window = block.split('"random_range"', 1)[1]
        self.assertIn('r.get("auto_upgrade_config")', window)

    def test_it_has_a_label(self):
        self.assertIn("auto_upgrade_time", STRINGS["entity"]["sensor"])

    def test_it_costs_no_extra_call(self):
        """The getter is already in the batched status request."""
        self.assertIn("getFirmwareAutoUpgradeConfig",
                      [name for name, _ in status.HUB_STATUS_REQUESTS])


if __name__ == "__main__":
    unittest.main()
