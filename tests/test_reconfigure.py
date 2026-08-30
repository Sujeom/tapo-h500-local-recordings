"""Moving a hub to a new address must not cost everything on the entry.

A hub that changed IP left one route: delete the entry and set it up again.
That loses every face name, every retention and download setting, and the
entity ids -- which takes every automation and dashboard card that named them
with it. A DHCP lease expiring should not cost that.

These drive the real step and read what it wrote, because "a blank password
keeps the stored one" is a fact about what was saved, not about what the
source says.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import ha_stubs  # noqa: E402
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

config_flow = importlib.import_module("tapo_h500.config_flow")

STORED = {
    "host": "192.168.11.5", "username": "admin",
    "password": "stored-camera", "cloud_password": "stored-cloud",
}


class _Entry:
    entry_id = "test"
    unique_id = "192.168.11.5"

    def __init__(self, **overrides):
        self.data = {**STORED, **overrides}
        self.title = "Tapo H500 (192.168.11.5)"
        self.options = {"face_names": {"7": "Sam"}, "keep_downloads": 25}


def _flow(entry=None, verdict=None, seen=None):
    flow = config_flow.TapoH500ConfigFlow()
    flow.hass = harness._Hass()
    flow.entry = entry or _Entry()
    flow.source = "reconfigure"

    async def validate(data):
        if seen is not None:
            seen.append(dict(data))
        return dict(verdict or {})

    flow._validate = validate
    return flow


def _run(flow, user_input=None):
    return asyncio.run(flow.async_step_reconfigure(user_input))


class TheForm(unittest.TestCase):
    def test_it_offers_the_address_and_the_credentials(self):
        result = _run(_flow())
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "reconfigure")
        fields = {str(key) for key in result["data_schema"].schema}
        self.assertEqual(
            fields, {"host", "username", "password", "cloud_password"})

    def test_the_address_starts_at_the_one_in_use(self):
        """Somebody changing a last octet should not retype the rest."""
        schema = _run(_flow())["data_schema"].schema
        defaults = {str(key): key.default() for key in schema
                    if key.default is not None}
        self.assertEqual(defaults["host"], "192.168.11.5")
        self.assertEqual(defaults["username"], "admin")

    def test_the_passwords_are_never_put_on_screen(self):
        """A stored password shown is a password shown to whoever is at the
        tablet, and this form's main job does not need one."""
        schema = _run(_flow())["data_schema"].schema
        defaults = {str(key): key.default() for key in schema
                    if key.default is not None}
        self.assertEqual(defaults["password"], "")
        self.assertEqual(defaults["cloud_password"], "")
        self.assertNotIn("stored-camera", str(schema))


class WhatItSaves(unittest.TestCase):
    def test_a_new_address_is_written_and_the_entry_reloads(self):
        flow = _flow()
        result = _run(flow, {"host": "192.168.11.9", "username": "admin",
                             "password": "", "cloud_password": ""})
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(flow.entry.data["host"], "192.168.11.9")
        self.assertTrue(flow.reloaded)

    def test_a_blank_password_keeps_the_stored_one(self):
        """The whole point of the form being usable for an address change."""
        flow = _flow()
        _run(flow, {"host": "192.168.11.9", "username": "admin",
                    "password": "", "cloud_password": ""})
        self.assertEqual(flow.entry.data["password"], "stored-camera")
        self.assertEqual(flow.entry.data["cloud_password"], "stored-cloud")

    def test_a_typed_password_replaces_it(self):
        flow = _flow()
        _run(flow, {"host": "192.168.11.5", "username": "admin",
                    "password": "new-one", "cloud_password": ""})
        self.assertEqual(flow.entry.data["password"], "new-one")
        self.assertEqual(flow.entry.data["cloud_password"], "stored-cloud")

    def test_the_stored_password_is_what_gets_tried(self):
        """A blank box must not reach the hub as an empty password."""
        seen = []
        _run(_flow(seen=seen), {"host": "192.168.11.9", "username": "admin",
                                "password": "", "cloud_password": ""})
        self.assertEqual(seen[0]["password"], "stored-camera")
        self.assertEqual(seen[0]["host"], "192.168.11.9")

    def test_the_title_follows_the_address(self):
        """Two hubs both called "Tapo H500 (192.168.11.5)" is a list nobody
        can use."""
        flow = _flow()
        _run(flow, {"host": "192.168.11.9", "username": "admin",
                    "password": "", "cloud_password": ""})
        self.assertEqual(flow.entry.title, "Tapo H500 (192.168.11.9)")

    def test_the_unique_id_moves_with_it(self):
        """It is the host. Left behind, a later setup at the new address
        would look like a different hub and be allowed alongside this one."""
        flow = _flow()
        _run(flow, {"host": "192.168.11.9", "username": "admin",
                    "password": "", "cloud_password": ""})
        self.assertEqual(flow.entry.unique_id, "192.168.11.9")

    def test_the_options_are_untouched(self):
        """Face names and retention live here, and losing them is the thing
        this step exists to prevent."""
        flow = _flow()
        _run(flow, {"host": "192.168.11.9", "username": "admin",
                    "password": "", "cloud_password": ""})
        self.assertEqual(flow.entry.options["face_names"], {"7": "Sam"})
        self.assertEqual(flow.entry.options["keep_downloads"], 25)


class WhatItRefuses(unittest.TestCase):
    def test_a_hub_that_will_not_answer_comes_back_as_a_form(self):
        flow = _flow(verdict={"base": "cannot_connect"})
        result = _run(flow, {"host": "192.168.11.9", "username": "admin",
                             "password": "", "cloud_password": ""})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        self.assertEqual(flow.entry.data["host"], "192.168.11.5",
                         "and nothing is saved")

    def test_what_was_typed_survives_the_error(self):
        """A wrong password must not also cost the address just entered."""
        flow = _flow(verdict={"base": "invalid_auth"})
        result = _run(flow, {"host": "192.168.11.9", "username": "operator",
                             "password": "wrong", "cloud_password": ""})
        defaults = {str(key): key.default() for key in
                    result["data_schema"].schema if key.default is not None}
        self.assertEqual(defaults["host"], "192.168.11.9")
        self.assertEqual(defaults["username"], "operator")

    def test_moving_it_onto_another_entrys_hub_is_refused(self):
        """Two entries on one hub means two pollers, each logging in to a
        device that wedges under repeated authentication."""
        flow = _flow()
        neighbour = _Entry(host="192.168.11.9")
        neighbour.entry_id = "another"
        flow.hass.config_entries.entries = [flow.entry, neighbour]
        result = _run(flow, {"host": "192.168.11.9", "username": "admin",
                             "password": "", "cloud_password": ""})
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "already_configured")
        self.assertEqual(flow.entry.data["host"], "192.168.11.5")

    def test_keeping_the_same_address_is_not_a_duplicate(self):
        """Changing only a password must not read as a collision with the
        entry doing the changing."""
        flow = _flow()
        flow.hass.config_entries.entries = [flow.entry]
        result = _run(flow, {"host": "192.168.11.5", "username": "admin",
                             "password": "new-one", "cloud_password": ""})
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "reconfigure_successful")


class ItIsNamed(unittest.TestCase):
    def test_the_step_and_its_outcomes_have_strings(self):
        import json
        for name in ("strings.json", "translations/en.json"):
            with self.subTest(name):
                doc = json.loads((Path(__file__).parents[1]
                                  / "custom_components" / "tapo_h500"
                                  / name).read_text())
                self.assertIn("reconfigure", doc["config"]["step"])
                self.assertIn("reconfigure_successful",
                              doc["config"]["abort"])
                self.assertIn("already_configured", doc["config"]["abort"])

    def test_every_field_on_the_form_has_a_label(self):
        import json
        doc = json.loads((Path(__file__).parents[1] / "custom_components"
                          / "tapo_h500" / "strings.json").read_text())
        labels = set(doc["config"]["step"]["reconfigure"]["data"])
        fields = {str(key) for key in
                  _run(_flow())["data_schema"].schema}
        self.assertEqual(fields - labels, set())


if __name__ == "__main__":
    unittest.main()
