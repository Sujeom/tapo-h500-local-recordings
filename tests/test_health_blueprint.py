"""The health blueprint: how a person finds out, separated from recovery."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
PATH = ROOT / "blueprints" / "automation" / "tapo_h500" / "watch_health.yaml"
RAW = PATH.read_text()


class _Loader(yaml.SafeLoader):
    """!input is Home Assistant's own tag; keep it as data so this parses."""


_Loader.add_constructor(
    "!input", lambda loader, node: {"__input__": loader.construct_scalar(node)})
DOC = yaml.load(RAW, Loader=_Loader)


class Structure(unittest.TestCase):
    def test_every_referenced_input_is_declared(self):
        declared = set(DOC["blueprint"]["input"])
        used = {node["__input__"] for node in _walk(DOC)}
        self.assertEqual(used - declared, set())

    def test_all_four_causes_have_a_trigger(self):
        ids = {trigger.get("id") for trigger in DOC["triggers"]}
        self.assertEqual(ids, {"silent", "media", "storage", "restarted"})

    def test_the_flapping_guard_exists(self):
        """A watchdog at its own threshold must not page anyone per poll."""
        silent = next(t for t in DOC["triggers"] if t.get("id") == "silent")
        self.assertIn("for", silent)

    def test_it_hears_the_automatic_restart(self):
        restarted = next(t for t in DOC["triggers"]
                         if t.get("id") == "restarted")
        self.assertEqual(restarted["event_type"], "tapo_h500_auto_restart")

    def test_each_cause_gets_its_own_words(self):
        body = str(DOC["actions"])
        for phrase in ("flat battery", "hub restart cures", "overwriting",
                       "restarted automatically"):
            self.assertIn(phrase.split()[0], body)

    def test_the_flat_battery_case_says_a_reboot_will_not_help(self):
        """The one wrong lesson this page must not teach."""
        body = str(DOC["actions"])
        self.assertIn("will not fix a flat battery", body)


def _walk(node):
    if isinstance(node, dict):
        if "__input__" in node:
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


if __name__ == "__main__":
    unittest.main()
