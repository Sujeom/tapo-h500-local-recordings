"""The subg probe cannot write, and cannot be made to.

It is pointed at a hub that stops responding under repeated authentication and
whose radio setters could plausibly unpair a camera. So the safety is not a
convention in the docstring: every request is checked before it reaches the
socket, and these are the checks.

Nothing here talks to a hub.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "probe_subg", str(ROOT / "tools" / "probe_subg.py"))
probe = importlib.util.module_from_spec(spec)
sys.modules["probe_subg"] = probe
spec.loader.exec_module(probe)


class NothingButReads(unittest.TestCase):
    def test_every_request_it_builds_is_a_get(self):
        for label, request in probe._requests():
            with self.subTest(label):
                self.assertTrue(probe._safe(request))

    def test_a_setter_is_refused(self):
        for method in ("setSubgConfig", "do", "setReboot", "deleteSubgDevice"):
            with self.subTest(method):
                self.assertFalse(probe._safe({"method": method, "params": {}}))

    def test_the_batching_envelope_is_allowed_as_a_carrier(self):
        """It performs nothing itself, and `executeFunction` builds one."""
        self.assertTrue(probe._safe({
            "method": "multipleRequest",
            "params": {"requests": [{"method": "getSubgStatus"}]}}))

    def test_but_only_as_a_carrier(self):
        self.assertFalse(probe._safe({
            "method": "multipleRequest",
            "params": {"requests": [{"method": "setSubgChannel"}]}}))

    def test_a_setter_hidden_inside_a_read_is_refused(self):
        """A batched request can carry sub-methods, so checking only the outer
        name would let a write through in a read-shaped envelope."""
        self.assertFalse(probe._safe({
            "method": "multipleRequest",
            "params": {"requests": [
                {"method": "getSubgStatus"},
                {"method": "setSubgChannel", "params": {"channel": 3}},
            ]},
        }))

    def test_a_setter_nested_under_a_namespace_is_refused(self):
        self.assertFalse(probe._safe(
            {"method": "get", "subg": {"do": {"unpair": "1"}}}))

    def test_a_setter_inside_a_list_is_refused(self):
        """Sub-requests arrive as a list, and a walk that stops at dicts
        would step straight over them."""
        self.assertFalse(probe._safe(
            {"method": "get",
             "params": {"requests": [{"method": "setSubgChannel"}]}}))
        self.assertFalse(probe._safe(
            {"method": "get", "subg": [{"do": {"unpair": "1"}}]}))


class WhatItAsks(unittest.TestCase):
    def test_it_tries_the_spellings_that_work_elsewhere_on_this_hub(self):
        """`app_component` and `general_camera_manage` answer to these five,
        and they are the only section names known to work here."""
        for section in ("config", "info", "status", "list", "subg"):
            self.assertIn(section, probe.SECTIONS)

    def test_it_tries_a_radios_own_vocabulary_too(self):
        for section in ("rf", "signal", "channel", "paired_list"):
            self.assertIn(section, probe.SECTIONS)

    def test_it_tries_the_method_route_as_well_as_the_namespace_route(self):
        """The two have found different things on this hub before."""
        self.assertTrue(probe.METHODS)
        self.assertTrue(all(name.startswith("get") for name in probe.METHODS))

    def test_it_knows_what_absent_looks_like(self):
        self.assertEqual(probe.NOT_A_METHOD, -40106)

    def test_it_knows_a_rejected_envelope_is_not_an_answer(self):
        """40210 means the hub never looked at the method, so it says nothing
        about whether it exists. Counting one as a hit would be a lie."""
        self.assertEqual(probe.ENVELOPE_REJECTED, 40210)

    def test_it_reads_both_spellings_of_the_error_code(self):
        """A rejected envelope comes back `err_code`; an evaluated one carries
        `error_code` inside. Reading only the second made a run of rejections
        look like a run of answers."""
        self.assertEqual(probe._error_code({"err_code": 40210}), 40210)
        self.assertEqual(probe._error_code({"error_code": -40106}), -40106)
        self.assertIsNone(probe._error_code({"subg": {}}))

    def test_it_asks_about_one_namespace(self):
        self.assertEqual(probe.NAMESPACE, "subg")


class OneLoginAndNoRetries(unittest.TestCase):
    SOURCE = (ROOT / "tools" / "probe_subg.py").read_text()

    def test_it_connects_once(self):
        self.assertEqual(self.SOURCE.count("client.connect()"), 1)

    def test_it_always_closes(self):
        self.assertIn("finally:\n        client.close()", self.SOURCE)

    def test_a_transport_failure_stops_the_run(self):
        """A hub that has stopped answering is a hub to leave alone."""
        self.assertIn("break", self.SOURCE.split("except Exception as err:", 1)[1])

    def test_it_takes_no_password_on_the_command_line(self):
        self.assertNotIn('add_argument("--password"', self.SOURCE)
        self.assertIn("getpass.getpass", self.SOURCE)


if __name__ == "__main__":
    unittest.main()
