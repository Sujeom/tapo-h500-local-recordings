"""What the hub says it is, which was fetched and then thrown away.

pytapo asks `getDeviceInfo` during login to work out what it is talking to, so
the model, firmware and hardware revision are already in hand before the first
poll. One model check read them and the rest was discarded -- and that check
read the wrong level of the reply, so it never ran at all.

The record is nested: `{"device_info": {"basic_info": {...}}}`. Reading
`device_model` off the outer dictionary finds nothing, the default fires, the
model comes back empty, and `if model and model != "H500"` is skipped. This
integration would have attached happily to a C200.
"""
import importlib
import re
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
API = (COMPONENT / "api.py").read_text()
SENSOR = (COMPONENT / "sensor.py").read_text()
DIAG = (COMPONENT / "diagnostics.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

status = importlib.import_module("tapo_h500.status")

# The shape the hub actually returns, as pytapo's own code reads it.
REPLY = {"device_info": {"basic_info": {
    "device_type": "SMART.TAPOHUB", "device_model": "H500",
    "device_alias": "Front Hall", "sw_version": "1.3.20 Build 250714 Rel.60872n",
    "hw_version": "1.0", "mac": "AA-BB-CC-DD-EE-FF"}}}


class Unwrapping(unittest.TestCase):
    def test_the_record_is_two_levels_down(self):
        self.assertEqual(status.basic_info(REPLY)["device_model"], "H500")

    def test_the_outer_dictionary_holds_nothing_useful(self):
        """The bug, stated as a fact. This is what the model check was
        reading."""
        self.assertIsNone(REPLY.get("device_model"))

    def test_a_flat_record_is_accepted_too(self):
        """pytapo has a KLAP branch that returns it flat, and its own code
        tests for both shapes."""
        flat = {"device_model": "H500", "sw_version": "1.3.20"}
        self.assertEqual(status.basic_info(flat)["device_model"], "H500")

    def test_nonsense_is_an_empty_record_rather_than_a_crash(self):
        """This runs during setup, where a raise is a config entry that will
        not load."""
        for junk in (None, [], "H500", 7):
            self.assertEqual(status.basic_info(junk), {})


class TheModelGuard(unittest.TestCase):
    def test_it_reads_the_unwrapped_record(self):
        body = API.split("def connect", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("basic_info(self._hub.basicInfo)", body)
        self.assertIn('self.info.get("device_model")', body)

    def test_it_still_refuses_another_model(self):
        body = API.split("def connect", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('if model and model != "H500":', body)

    def test_a_hub_that_does_not_say_is_allowed_through(self):
        """An empty model means the hub did not answer that field, not that it
        is the wrong device. Refusing there would lock out a firmware that
        renamed one key."""
        self.assertEqual(status.basic_info({"sw_version": "1"})
                         .get("device_model"), None)


class OnTheDevicePage(unittest.TestCase):
    def test_the_firmware_version_is_published(self):
        """Home Assistant's device page has a Firmware field and it was
        empty."""
        body = SENSOR.split("def hub_device", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('sw_version=info.get("sw_version")', body)
        self.assertIn('hw_version=info.get("hw_version")', body)

    def test_the_model_comes_from_the_hub(self):
        body = SENSOR.split("def hub_device", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('model=info.get("device_model") or "H500"', body)

    def test_it_costs_no_extra_round_trip(self):
        """Read off the client, which already holds it from login. Adding a
        getter to the status poll would spend a call on a value that cannot
        change while the integration is loaded."""
        body = SENSOR.split("def hub_device", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("coordinator.client.info", body)
        # Not added to the once-a-minute status batch: the answer cannot
        # change while the integration is loaded, and this hub is easy to
        # overload.
        self.assertNotIn("getDeviceInfo",
                         [name for name, _ in status.HUB_STATUS_REQUESTS])


class InTheBugReport(unittest.TestCase):
    def test_the_versions_are_included(self):
        """The first thing anyone reading a report about an undocumented
        protocol wants to know."""
        self.assertIn("SAFE_DEVICE", DIAG)
        listed = set(re.findall(
            r'"([a-z0-9_]+)"',
            DIAG.split("SAFE_DEVICE = (", 1)[1].split(")", 1)[0]))
        self.assertEqual(listed, {"device_model", "sw_version", "hw_version"})

    def test_nothing_identifying_goes_with_them(self):
        """The record also carries a MAC, a device id and the owner's own name
        for the hub."""
        for private in ("mac", "dev_id", "device_alias", "oem_id", "hw_id"):
            self.assertNotIn(f'"{private}"', DIAG, private)

    def test_it_is_still_an_allow_list(self):
        self.assertIn("for key in SAFE_DEVICE", DIAG)


if __name__ == "__main__":
    unittest.main()
