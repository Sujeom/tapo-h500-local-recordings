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
import ast
import re
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
EN = json.loads((COMPONENT / "translations" / "en.json").read_text())


TRANSLATIONS = {path.stem: json.loads(path.read_text())
                for path in sorted((COMPONENT / "translations").glob("*.json"))}
SOURCE = {path.name: path.read_text()
          for path in sorted(COMPONENT.glob("*.py"))}


def _named(text: str) -> set[str]:
    """The {placeholders} a message asks to be given."""
    return set(re.findall(r"\{(\w+)\}", text))


def _exception_raises():
    """Every raise that names a translation key, with the placeholders it
    supplies. Parsed from the syntax tree rather than matched by regex: the
    placeholders are a dict literal spread over several lines."""
    keys, supplied = set(), {}
    for source in SOURCE.values():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            found = {kw.arg: kw.value for kw in node.exc.keywords}
            key = found.get("translation_key")
            if not isinstance(key, ast.Constant):
                continue
            keys.add(key.value)
            holder = found.get("translation_placeholders")
            names = set()
            if isinstance(holder, ast.Dict):
                names = {k.value for k in holder.keys
                         if isinstance(k, ast.Constant)}
            supplied[key.value] = names
    return keys, supplied


_RAISED_KEYS, _PLACEHOLDERS_AT_RAISE = _exception_raises()
_PLACEHOLDERS_IN_MESSAGES = {
    key: _named(body["message"])
    for key, body in EN.get("exceptions", {}).items()
    if key in _RAISED_KEYS
}


def _leaves(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{prefix}{key}.")
    else:
        yield prefix.rstrip("."), node


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


LANGUAGES = {path.stem: json.loads(path.read_text())
             for path in sorted((COMPONENT / "translations").glob("*.json"))
             if path.stem != "en"}


class EveryLanguageIsComplete(unittest.TestCase):
    """A shipped language has to say everything, not most things.

    Home Assistant's fallback for a key a language file lacks is not
    something this project can test from here, and the failure if it does not
    fall back is a blank label -- worse than English. So the rule is simple
    enough to hold by hand: a language is complete or it is not shipped.
    """

    def test_there_is_more_than_english(self):
        self.assertTrue(LANGUAGES, "no translations beyond English")

    def test_none_of_them_is_missing_anything(self):
        for language, doc in LANGUAGES.items():
            with self.subTest(language):
                self.assertEqual(
                    sorted(set(paths(EN)) - set(paths(doc))), [],
                    "every key English has, this one has")

    def test_none_of_them_says_anything_english_does_not(self):
        """A key English lacks is a typo. Nothing will ever show it."""
        for language, doc in LANGUAGES.items():
            with self.subTest(language):
                self.assertEqual(
                    sorted(set(paths(doc)) - set(paths(EN))), [])

    def test_every_placeholder_survives(self):
        """`{cameras}` translated into prose is a notice that renders the
        brace and tells nobody which camera."""
        for language, doc in LANGUAGES.items():
            english = dict(_leaves(EN))
            for path, text in _leaves(doc):
                with self.subTest(f"{language}:{path}"):
                    self.assertEqual(
                        set(re.findall(r"\{(\w+)\}", str(text))),
                        set(re.findall(r"\{(\w+)\}", str(english[path]))))

    def test_nothing_was_left_in_english(self):
        """Not a hard rule -- a few words are the same in both -- but a whole
        long description identical to the English one is an untranslated
        block that slipped through."""
        for language, doc in LANGUAGES.items():
            english = dict(_leaves(EN))
            untranslated = [path for path, text in _leaves(doc)
                            if len(str(text)) > 80
                            and str(text) == str(english[path])]
            with self.subTest(language):
                self.assertEqual(untranslated, [])


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
        """A notice carries a title and then either a description or a fix
        flow -- Home Assistant's schema treats those two as alternatives, and
        hassfest rejects a notice that has both."""
        for key, body in EN["issues"].items():
            with self.subTest(key):
                self.assertTrue(body.get("title", "").strip())
                if "fix_flow" in body:
                    self.assertNotIn("description", body)
                    said = (body["fix_flow"]["step"]["init"]
                            .get("description", ""))
                else:
                    said = body.get("description", "")
                self.assertTrue(said.strip())

    def test_every_entity_translation_key_resolves(self):
        """Anywhere under entity, issues or selector. A missing one is not an
        error: Home Assistant shows the raw key, and an entity called
        `component.tapo_h500.entity.sensor.hub_health` is one nobody finds."""
        worded = ({key for table in EN.get("entity", {}).values()
                   for key in table}
                  | set(EN.get("issues", {})) | set(EN.get("selector", {}))
                  # Exceptions carry a key the same way, and one that does
                  # not resolve puts a raw key in front of somebody at the
                  # moment something has already gone wrong.
                  | set(EN.get("exceptions", {})))
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



class EveryExceptionSaysSomething(unittest.TestCase):
    """The messages people see when something has already gone wrong.

    The German translation is complete for everything else; before this,
    every error a German user actually hit arrived in English.
    """

    def test_every_exception_key_is_worded_in_english(self):
        raised = {key for key in _RAISED_KEYS}
        self.assertTrue(raised)
        self.assertEqual(sorted(raised - set(EN.get("exceptions", {}))), [])

    def test_and_in_german(self):
        """A missing key falls back silently, so nothing says the German
        user is reading English."""
        for language, table in TRANSLATIONS.items():
            with self.subTest(language=language):
                self.assertEqual(
                    sorted(set(_RAISED_KEYS) - set(table.get("exceptions", {}))),
                    [])

    def test_no_message_is_left_empty(self):
        for language, table in TRANSLATIONS.items():
            for key, body in table.get("exceptions", {}).items():
                with self.subTest(language=language, key=key):
                    self.assertTrue(str(body.get("message", "")).strip())

    def test_every_placeholder_is_supplied_where_it_is_raised(self):
        """A message with {host} and no placeholder renders the braces."""
        for key, needed in _PLACEHOLDERS_IN_MESSAGES.items():
            with self.subTest(key=key):
                self.assertEqual(needed, _PLACEHOLDERS_AT_RAISE.get(key, set()))

    def test_the_languages_ask_for_the_same_placeholders(self):
        """A translation that invents one renders empty braces in that
        language only, which is the hardest kind to notice."""
        english = {key: _named(body["message"])
                   for key, body in EN.get("exceptions", {}).items()}
        for language, table in TRANSLATIONS.items():
            for key, body in table.get("exceptions", {}).items():
                with self.subTest(language=language, key=key):
                    self.assertEqual(_named(body["message"]), english[key])


if __name__ == "__main__":
    unittest.main()
