"""The hub's own reboot schedule, which nothing was reading.

`getReboot` answers `{"enabled":"off","day":"0","time":"03:00:00"}` and was
never asked. It matters more than a settings dump usually does: a hub that
reboots itself has a gap in its recordings at that hour, and a gap in
recordings is indistinguishable from a camera that stopped working -- which is
exactly what the silent-camera watchdog would call it.

Read only, and it stays that way. `setReboot` is not called from anywhere:
its params (`timing_reboot`) are ambiguous between scheduling a reboot and
performing one, and a wrong guess reboots the hub mid-download.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "sensor.py").read_text()
API = (COMPONENT / "api.py").read_text()
DIAG = (COMPONENT / "diagnostics.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

status = importlib.import_module("tapo_h500.status")


def reply(**block):
    return {"getReboot": {"timing_reboot": {"reboot": block}}}


class Schedule(unittest.TestCase):
    def test_a_schedule_that_is_on_reports_its_hour(self):
        readings = status.hub_readings(
            reply(enabled="on", day="0", time="03:00:00"))
        self.assertEqual(readings["scheduled_reboot"], "03:00:00")
        self.assertIs(readings["scheduled_reboot_enabled"], True)

    def test_a_schedule_that_is_off_says_off(self):
        """The time is stored either way. Showing "03:00:00" for a hub that is
        not going to reboot is the more alarming of the two wrong answers."""
        readings = status.hub_readings(
            reply(enabled="off", day="0", time="03:00:00"))
        self.assertEqual(readings["scheduled_reboot"], "off")

    def test_a_hub_that_said_nothing_is_unknown_not_off(self):
        """These params came from pytapo rather than a live probe, so an
        unanswered call has to read as unknown -- claiming "off" would be
        inventing a reassurance."""
        self.assertIsNone(status.hub_readings({})["scheduled_reboot"])
        self.assertIsNone(status.hub_readings(reply())["scheduled_reboot"])

    def test_a_schedule_on_with_no_time_still_reports_something(self):
        self.assertEqual(
            status.hub_readings(reply(enabled="on"))["scheduled_reboot"], "on")

    def test_the_day_is_passed_through_untranslated(self):
        """The only value ever seen was 0, on a schedule that was switched
        off, which says nothing about which day 0 means."""
        readings = status.hub_readings(reply(enabled="on", day="3"))
        self.assertEqual(readings["scheduled_reboot_day"], "3")

    def test_a_failed_sub_response_is_survivable(self):
        """unpack_multiple drops any sub-response that did not answer 0, so a
        wrong param shape costs this reading and nothing else."""
        readings = status.hub_readings({"getSirenStatus": {"status": "off"}})
        self.assertIsNone(readings["scheduled_reboot"])
        self.assertIsNotNone(readings["siren_active"])


class Fetched(unittest.TestCase):
    def test_it_rides_the_batched_status_request(self):
        """One multipleRequest costs the same as one getter, and this hub is
        easy to wedge."""
        self.assertIn("getReboot",
                      [name for name, _ in status.HUB_STATUS_REQUESTS])

    def test_it_uses_pytapo_s_own_params(self):
        params = dict(status.HUB_STATUS_REQUESTS)["getReboot"]
        self.assertEqual(params, {"timing_reboot": {"name": ["reboot"]}})

    def test_nothing_writes_it(self):
        """The one setter deliberately never called.

        Matched as a quoted method name, which is how a call would appear.
        The bare word occurs in three comments explaining why it is not
        called, and asserting against that passed while proving nothing --
        the same trap a condition test here fell into once already.
        """
        for source in (API, SOURCE, (COMPONENT / "status.py").read_text()):
            self.assertNotIn('"setReboot"', source)


class Entity(unittest.TestCase):
    def test_the_sensor_exists_and_is_diagnostic(self):
        block = SOURCE.split('key="scheduled_reboot"', 1)[1] \
                      .split("\n    ),", 1)[0]
        self.assertIn("EntityCategory.DIAGNOSTIC", block)

    def test_it_carries_the_raw_day_and_the_flag(self):
        block = SOURCE.split('key="scheduled_reboot"', 1)[1] \
                      .split("\n    ),", 1)[0]
        self.assertIn('"day": r.get("scheduled_reboot_day")', block)
        self.assertIn('"enabled": r.get("scheduled_reboot_enabled")', block)

    def test_it_has_a_label(self):
        self.assertIn("scheduled_reboot", STRINGS["entity"]["sensor"])

    def test_the_bug_report_carries_it(self):
        """It explains a gap in recordings that would otherwise be read as a
        camera fault."""
        listed = DIAG.split("SAFE_READINGS = (", 1)[1].split(")", 1)[0]
        self.assertIn('"scheduled_reboot"', listed)


if __name__ == "__main__":
    unittest.main()


class FirmwareUpgradeInfo(unittest.TestCase):
    """What the cloud check said, read without guessing too hard.

    Probed 2026-08-17: checkFirmwareVersionByCloud + getCloudConfig
    upgrade_info both answer error_code 0, and an up-to-date hub returns an
    EMPTY upgrade_info. The field names of a pending update are unknown
    until one exists, so the parser tries the plausible spellings and keeps
    the raw block for the entity to expose either way.
    """

    def test_an_empty_block_is_up_to_date(self):
        info = status.firmware_upgrade({"getCloudConfig": {
            "cloud_config": {"upgrade_info": {}}}})
        self.assertIsNone(info["version"])
        self.assertEqual(info["raw"], {})

    def test_a_named_version_comes_out(self):
        for key in ("firmware_version", "version", "fw_version"):
            info = status.firmware_upgrade({"getCloudConfig": {
                "cloud_config": {"upgrade_info": {key: "1.4.0"}}}})
            self.assertEqual(info["version"], "1.4.0", key)

    def test_a_shape_never_seen_keeps_the_evidence(self):
        info = status.firmware_upgrade({"getCloudConfig": {
            "cloud_config": {"upgrade_info": {"mystery": "x"}}}})
        self.assertIsNone(info["version"])
        self.assertEqual(info["raw"], {"mystery": "x"})

    def test_no_answer_is_no_answer(self):
        self.assertIsNone(status.firmware_upgrade({})["version"])
        self.assertEqual(status.firmware_upgrade({})["raw"], {})


class UpdateEntityWiring(unittest.TestCase):
    UPDATE = (COMPONENT / "update.py").read_text()
    INIT = (COMPONENT / "__init__.py").read_text()

    def test_the_platform_is_registered(self):
        self.assertIn("Platform.UPDATE", self.INIT)

    def test_an_empty_cloud_answer_reads_as_up_to_date(self):
        body = self.UPDATE.split("def latest_version", 1)[1]
        self.assertIn("or self.installed_version", body)

    def test_it_reports_and_never_installs(self):
        """setFirmwareUpgrade is deliberately unprobed on a hub that is easy
        to wedge; the app does the upgrading."""
        self.assertNotIn("async_install", self.UPDATE)
        # No hub calls at all from this entity -- it reads what the
        # coordinator fetched, and nothing else. (Asserted on the call
        # shapes, not on prose: a docstring may NAME the setter it refuses.)
        self.assertNotIn("executeFunction", self.UPDATE)
        self.assertNotIn("performRequest", self.UPDATE)

    def test_the_raw_cloud_answer_is_visible(self):
        self.assertIn('"upgrade_info"', self.UPDATE)
