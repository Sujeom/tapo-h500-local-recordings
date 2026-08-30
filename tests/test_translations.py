"""Every string the integration shows exists, and both copies of it agree.

`strings.json` is the developer-facing source and `translations/en.json` is
what the frontend actually loads. Nothing kept them together, so they drifted:
102 keys existed only in the one users see, including every one of the nine
repair notices, and six existed only in the one they do not. The gate checked
entity translation keys and nothing else, so the drift was invisible.

A missing string is not an error anywhere. Home Assistant falls back to the
raw key, and a repair notice reading `component.tapo_h500.issues.media_wedged`
is a notice nobody acts on.
"""
import json
import re
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
EN = json.loads((COMPONENT / "translations" / "en.json").read_text())


def paths(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from paths(value, f"{prefix}{key}.")
    else:
        yield prefix.rstrip(".")


class TheTwoCopiesAgree(unittest.TestCase):
    """They are the same English. Anything else is a drift waiting to
    happen, and it happened."""

    def test_neither_has_a_key_the_other_lacks(self):
        self.assertEqual(sorted(set(paths(EN)) - set(paths(STRINGS))), [])
        self.assertEqual(sorted(set(paths(STRINGS)) - set(paths(EN))), [])

    def test_they_say_the_same_thing(self):
        """A key in both with different words is worse than a missing one:
        it looks maintained."""
        self.assertEqual(STRINGS, EN)


class EverythingShownHasWords(unittest.TestCase):
    SOURCES = {path.name: path.read_text()
               for path in sorted(COMPONENT.glob("*.py"))}

    def _all(self, pattern):
        found = set()
        for source in self.SOURCES.values():
            found |= set(re.findall(pattern, source))
        return found

    def test_every_repair_notice_is_worded(self):
        """These are named by constant, not by literal, so the gate's regex
        over `translation_key="..."` never saw one. All nine existed only in
        the file users read and not in the one a developer opens."""
        constants = {}
        for source in self.SOURCES.values():
            constants.update(re.findall(r'^(\w*ISSUE)\s*=\s*"(\w+)"',
                                        source, re.M))
        used = {constants[name]
                for name in self._all(r'translation_key=(\w*ISSUE)')
                if name in constants}
        self.assertTrue(used, "repair notices are raised at all")
        self.assertEqual(used - set(EN["issues"]), set())

    def test_every_notice_says_what_to_do_as_well_as_what_happened(self):
        for key, body in EN["issues"].items():
            with self.subTest(key):
                self.assertTrue(body.get("title", "").strip())
                self.assertTrue(body.get("description", "").strip())

    def test_every_entity_translation_key_resolves(self):
        """Anywhere under entity, issues or selector. A missing one is not an
        error: Home Assistant shows the raw key, and an entity called
        `component.tapo_h500.entity.sensor.hub_health` is one nobody finds."""
        worded = ({key for table in EN.get("entity", {}).values()
                   for key in table}
                  | set(EN.get("issues", {})) | set(EN.get("selector", {})))
        declared = self._all(r'translation_key="(\w+)"')
        self.assertTrue(declared)
        self.assertEqual(sorted(declared - worded), [])

    def test_every_field_on_a_form_has_a_label(self):
        for section in ("config", "options"):
            for step, body in EN.get(section, {}).get("step", {}).items():
                for field, label in (body.get("data") or {}).items():
                    with self.subTest(f"{section}.{step}.{field}"):
                        self.assertTrue(str(label).strip(),
                                        "an unlabelled field shows its raw key")

    def test_a_step_that_collects_nothing_still_explains_itself(self):
        """A form with no fields is a page of prose, and a blank one is a
        dead end."""
        for section in ("config", "options"):
            for step, body in EN.get(section, {}).get("step", {}).items():
                if body.get("data"):
                    continue
                with self.subTest(f"{section}.{step}"):
                    self.assertTrue(
                        (body.get("description") or body.get("title") or ""
                         ).strip())


if __name__ == "__main__":
    unittest.main()
