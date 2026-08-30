"""Which glyph each entity and action shows.

Home Assistant reads these from icons.json rather than from the entity, which
buys two things the attribute cannot: an icon that varies by state, and an
icon the frontend knows before the entity has one -- which is what removes
the flicker on a freshly loaded dashboard.

The failure mode is silence. A key naming an entity that does not exist, or a
state the entity never reports, renders nothing and warns about nothing.
"""
import ast
import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "tapo_h500"
ICONS = json.loads((COMPONENT / "icons.json").read_text())
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
ACTIONS = yaml.safe_load((COMPONENT / "services.yaml").read_text())


def _keys_in_code(platform: str) -> set[str]:
    """Translation keys the platform's entities actually set."""
    found = set()
    source = (COMPONENT / f"{platform}.py").read_text()
    for match in re.finditer(r'translation_key\s*=\s*"([\w]+)"', source):
        found.add(match.group(1))
    for match in re.finditer(r'translation_key="([\w]+)"', source):
        found.add(match.group(1))
    return found


class EveryIconBelongsToSomething(unittest.TestCase):
    def test_each_entity_key_is_one_an_entity_really_uses(self):
        """A key nothing sets files an icon under a name the frontend never
        asks for."""
        for platform, entries in ICONS["entity"].items():
            in_code = _keys_in_code(platform)
            for key in entries:
                with self.subTest(platform=platform, key=key):
                    self.assertIn(key, in_code)

    def test_each_entity_key_is_also_a_translated_one(self):
        """The same key names the entity. One without a name is an entity
        showing a raw key, so the two files have to agree."""
        for platform, entries in ICONS["entity"].items():
            named = set(STRINGS["entity"].get(platform, {}))
            for key in entries:
                with self.subTest(platform=platform, key=key):
                    self.assertIn(key, named)

    def test_every_action_has_one(self):
        """They appear in the Actions list, where the default is a generic
        glyph for all thirteen."""
        self.assertEqual(set(ICONS["services"]), set(ACTIONS))

    def test_every_icon_is_an_mdi_name(self):
        def walk(node):
            if isinstance(node, str):
                yield node
            elif isinstance(node, dict):
                for value in node.values():
                    yield from walk(value)
        for icon in walk(ICONS):
            with self.subTest(icon=icon):
                self.assertRegex(icon, r"^mdi:[a-z0-9-]+$")


class StateIconsNameRealStates(unittest.TestCase):
    """An icon filed under a state the entity never reports never renders,
    and nothing says so."""

    BINARY = {"on", "off"}

    def test_a_binary_sensors_states_are_on_and_off(self):
        for key, entry in ICONS["entity"].get("binary_sensor", {}).items():
            with self.subTest(key=key):
                self.assertLessEqual(set(entry.get("state", {})), self.BINARY)

    def test_a_switchs_states_are_on_and_off(self):
        for key, entry in ICONS["entity"].get("switch", {}).items():
            with self.subTest(key=key):
                self.assertLessEqual(set(entry.get("state", {})), self.BINARY)

    def test_every_entry_has_a_default(self):
        """A state map with no default leaves the entity iconless in every
        state that is not listed."""
        for platform, entries in ICONS["entity"].items():
            for key, entry in entries.items():
                with self.subTest(platform=platform, key=key):
                    self.assertIn("default", entry)


class TheCodeDoesNotSetThemToo(unittest.TestCase):
    def test_only_the_entity_with_no_translation_key_still_does(self):
        """Both means the code wins and icons.json is dead. The one exception
        builds its name dynamically, so there is no key to file under."""
        still_setting = []
        for path in sorted(COMPONENT.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Assign)
                        and any(getattr(t, "id", "") == "_attr_icon"
                                for t in node.targets)):
                    still_setting.append(f"{path.name}:{node.lineno}")
        self.assertEqual(len(still_setting), 1, still_setting)
        self.assertTrue(still_setting[0].startswith("sensor.py"))


if __name__ == "__main__":
    unittest.main()
