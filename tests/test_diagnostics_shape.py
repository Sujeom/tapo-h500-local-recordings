"""A field nobody has named yet is still visible in a bug report.

Redaction here is by allow-list, which is the right way to keep somebody's
installation out of a public file: nothing reaches it unless it was named. It
has one cost, and it is a real one. A field the parser does not know about is
invisible, so nobody can add it to the list because nobody knows it exists.
That is exactly how `detect_status` went unnoticed until it was found by
dumping the hub's JSON by hand.

Paths and types give that away without giving anything else away. The fix for
a key that turns out to matter is still to name it in the allow-list -- this
makes the list maintainable rather than replacing it.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

diagnostics = importlib.import_module("tapo_h500.diagnostics")
shape = diagnostics._shape


class TheShape(unittest.TestCase):
    def test_a_field_the_parser_ignores_still_appears(self):
        """The whole point. `detect_status` is a real key from a real hub
        that no reading is built from."""
        described = shape({"getSdCardStatus": {"harddisk_manage": {
            "hd_info": {"detect_status": "failed", "status": "normal"}}}})
        self.assertIn(
            "getSdCardStatus.harddisk_manage.hd_info.detect_status", described)

    def test_the_value_itself_never_appears(self):
        described = shape({"system": {"cloud_username": "someone@example.com",
                                      "ssid": "Our House"}})
        self.assertNotIn("someone@example.com", str(described))
        self.assertNotIn("Our House", str(described))
        self.assertEqual(described["system.cloud_username"], "str")

    def test_types_are_named(self):
        described = shape({"a": 1, "b": 1.5, "c": True, "d": "x", "e": None})
        self.assertEqual(
            [described[k] for k in "abcde"],
            ["int", "float", "bool", "str", "NoneType"])

    def test_one_entry_stands_for_a_list(self):
        """A hub with sixteen cameras would otherwise repeat one shape
        sixteen times, and the length is the interesting part."""
        described = shape({"cameras": [{"mac": "x"}, {"mac": "y"},
                                       {"mac": "z"}]})
        self.assertEqual(described, {"cameras.[3].mac": "str"})

    def test_an_empty_list_says_so(self):
        """Rather than vanishing, which reads as "the hub did not send it"."""
        self.assertEqual(shape({"cameras": []}), {"cameras": "list(0)"})

    def test_it_is_bounded(self):
        described = shape({str(n): n for n in range(diagnostics.SHAPE_LIMIT * 2)})
        self.assertLessEqual(len(described), diagnostics.SHAPE_LIMIT + 1)
        self.assertIn("truncated", described)

    def test_a_hub_that_has_answered_nothing_yet_is_empty(self):
        self.assertEqual(shape({}), {})


class TheWiring(unittest.TestCase):
    def test_the_coordinator_keeps_the_raw_answer(self):
        coord, _ = harness._build()
        self.assertEqual(coord.raw_status, {})

    def test_a_poll_fills_it_in(self):
        import asyncio
        coord, client = harness._build()
        client.hub_status = lambda: {"getLedStatus": {"led": {"config": {
            "enabled": "on"}}}}
        asyncio.run(coord._async_update_data())
        self.assertIn("getLedStatus", coord.raw_status)
        self.assertEqual(
            shape(coord.raw_status)["getLedStatus.led.config.enabled"], "str")

    def test_diagnostics_include_it(self):
        source = (Path(__file__).parents[1] / "custom_components" /
                  "tapo_h500" / "diagnostics.py").read_text()
        self.assertIn('"hub_answer_shape": _shape(', source)

    def test_the_allow_list_is_still_how_values_get_out(self):
        """This is a discovery aid, not a replacement. If it ever started
        carrying values it would undo the redaction it sits beside."""
        described = shape({"getSirenConfig": {"volume": 7}})
        self.assertNotIn("7", str(list(described.values())))


if __name__ == "__main__":
    unittest.main()
