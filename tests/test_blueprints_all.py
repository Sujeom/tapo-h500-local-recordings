"""Invariants every blueprint must hold, applied to all of them at once.

Each blueprint has a test file about what it MEANS -- which detections notify,
when the announce gate opens. What none of them covered is the structural
floor, and that gap grows silently: `watch_health.yaml` never got the
input-declaration check `notify_on_detection.yaml` has, so a typo'd `!input`
in it would reach a user as "blueprint refuses to import" rather than as a
failing test here.

Applied by discovery rather than by name, so a blueprint added tomorrow is
covered the moment it lands.
"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
BLUEPRINTS = sorted((ROOT / "blueprints" / "automation" / "tapo_h500")
                    .glob("*.yaml"))
EXAMPLES = sorted((ROOT / "examples").glob("*.yaml"))


class _Loader(yaml.SafeLoader):
    """!input is Home Assistant's own tag; keep it as data so this parses."""


_Loader.add_constructor(
    "!input", lambda loader, node: {"__input__": loader.construct_scalar(node)})


def documents():
    for path in BLUEPRINTS:
        yield path, yaml.load(path.read_text(), Loader=_Loader)


class EveryBlueprint(unittest.TestCase):
    def test_there_are_some(self):
        """A discovery-driven suite that finds nothing passes silently."""
        self.assertGreaterEqual(len(BLUEPRINTS), 4)

    def test_each_declares_where_it_came_from(self):
        """Without source_url Home Assistant cannot offer to re-import it,
        which is how a fixed blueprint never reaches anyone."""
        for path, doc in documents():
            with self.subTest(path.name):
                self.assertIn("source_url", doc["blueprint"], path.name)
                self.assertEqual(doc["blueprint"]["domain"], "automation")
                # Home Assistant nests it under `homeassistant:`.
                self.assertIn(
                    "min_version",
                    doc["blueprint"].get("homeassistant", {}), path.name)

    def test_every_referenced_input_is_declared(self):
        """An undeclared !input makes the blueprint refuse to import."""
        for path, doc in documents():
            with self.subTest(path.name):
                declared = set(doc["blueprint"].get("input", {}))
                used = set(re.findall(r"!input (\w+)", path.read_text()))
                self.assertEqual(used - declared, set(), path.name)

    def test_every_declared_input_is_used(self):
        """A declared input nothing reads is a control that does nothing."""
        for path, doc in documents():
            with self.subTest(path.name):
                declared = set(doc["blueprint"].get("input", {}))
                used = set(re.findall(r"!input (\w+)", path.read_text()))
                self.assertEqual(declared - used, set(), path.name)

    def test_each_declares_a_mode(self):
        """The default is `single`, which silently drops a second doorbell
        press arriving while the first is still being notified."""
        for path, doc in documents():
            with self.subTest(path.name):
                self.assertIn("mode", doc, path.name)

    def test_each_uses_the_plural_trigger_syntax(self):
        """`triggers:`/`conditions:`/`actions:`, not the pre-2024.10 singular.
        Both work today; only one keeps working."""
        for path, doc in documents():
            with self.subTest(path.name):
                self.assertIn("triggers", doc, path.name)
                self.assertNotIn("trigger", doc, path.name)
                self.assertNotIn("action", doc, path.name)


class NoDeprecatedCalls(unittest.TestCase):
    """`service:` was renamed to `action:` in 2024.8.

    Checked structurally rather than by grepping the text: three blueprints
    legitimately assign `service: !input notify_service` inside `variables:`,
    and a text search calls those violations.
    """

    @staticmethod
    def _steps(node):
        """Every mapping that looks like an action step, anywhere."""
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from NoDeprecatedCalls._steps(value)
        elif isinstance(node, list):
            for item in node:
                yield from NoDeprecatedCalls._steps(item)

    def test_no_action_step_calls_service(self):
        for path in BLUEPRINTS + EXAMPLES:
            doc = yaml.load(path.read_text(), Loader=_Loader)
            with self.subTest(path.name):
                for step in self._steps(doc.get("actions") or doc.get("action")
                                        or []):
                    self.assertNotIn(
                        "service", step,
                        f"{path.name}: use `action:` for a service call")


class EveryExample(unittest.TestCase):
    def test_they_parse_and_declare_a_mode(self):
        for path in EXAMPLES:
            with self.subTest(path.name):
                doc = yaml.load(path.read_text(), Loader=_Loader)
                self.assertIn("alias", doc, path.name)
                self.assertIn("triggers", doc, path.name)

    def test_no_example_carries_a_real_identifier(self):
        """These get pasted into issues and forum posts."""
        for path in EXAMPLES + BLUEPRINTS:
            text = path.read_text()
            with self.subTest(path.name):
                self.assertNotIn("192.168.", text, path.name)


if __name__ == "__main__":
    unittest.main()
