"""The documentation says things that are still true.

Three of these were not. The notes said in one section that `alarm_type` 17 is
a doorbell press, confirmed against a real one, and in two others that which
code means a press was still unknown -- the file contradicted itself. The
verification page quoted 99 tests when there were 1,470, and 43 card checks
when there were 116. The README named two entities by ids Home Assistant does
not build.

None of that is cosmetic. Documentation nobody can trust is documentation
nobody reads, and every one of these was somebody's answer to "does this work
yet?"
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "tapo_h500"
EN = json.loads((COMPONENT / "translations" / "en.json").read_text())
DOCS = {path.name: path.read_text() for path in ROOT.glob("*.md")}
DOCS.update({path.name: path.read_text() for path in (ROOT / "docs").glob("*.md")})


def slug(name: str) -> str:
    """What Home Assistant makes of a translated entity name."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class TheEntityIdsAreTheRealOnes(unittest.TestCase):
    """`has_entity_name` builds an id from the device name and the entity's
    translated name, so an id in the docs is a claim about a translation."""

    SLUGS = {platform: {slug(body["name"])
                        for body in table.values() if "name" in body}
             for platform, table in EN["entity"].items()}
    PATTERN = re.compile(
        r"`(sensor|binary_sensor|image|switch|camera|select|number|siren|"
        r"calendar|update|button|event)\.<(?:hub|camera)>_(\w+)`")

    def test_every_id_the_docs_name_is_one_the_integration_makes(self):
        wrong = []
        for name, text in DOCS.items():
            for platform, tail in self.PATTERN.findall(text):
                if tail not in self.SLUGS.get(platform, set()):
                    wrong.append(f"{name}: {platform}.<x>_{tail}")
        self.assertEqual(wrong, [])

    def test_the_docs_name_some_at_all(self):
        found = sum(len(self.PATTERN.findall(text)) for text in DOCS.values())
        self.assertGreater(found, 3, "otherwise this test proves nothing")

    def test_the_check_would_catch_a_renamed_entity(self):
        self.assertNotIn("auto_upgrade", self.SLUGS["switch"],
                         "the switch is called Automatic firmware updates")
        self.assertIn("automatic_firmware_updates", self.SLUGS["switch"])


class TheCountsAreNotTenTimesStale(unittest.TestCase):
    """Written as a floor -- "over 1,400" -- so adding a test does not make
    the documentation wrong. What is checked is that the floor is a floor and
    that it has not been left a decade behind."""

    @staticmethod
    def _claimed(text, pattern):
        found = re.search(pattern, text)
        return int(found.group(1).replace(",", "")) if found else None

    def _python_tests(self):
        """Counted by loading them, not by running them.

        Shelling out to `unittest discover` from inside a test it discovers
        is a suite that runs itself until something gives up.
        """
        loader = unittest.TestLoader()
        return loader.discover(str(ROOT / "tests")).countTestCases()

    def _card_checks(self):
        result = subprocess.run(
            ["node", str(ROOT / "tests" / "test_cards.mjs")],
            capture_output=True, text=True, cwd=ROOT)
        return result.stdout.count("\n  ok ")

    def test_the_python_count_is_a_floor_and_a_recent_one(self):
        claimed = self._claimed(DOCS["limitations.md"],
                                r"over ([\d,]+)\s*\n?tests")
        self.assertIsNotNone(claimed, "no test count in limitations.md")
        actual = self._python_tests()
        self.assertLessEqual(claimed, actual, "the floor is above the truth")
        self.assertGreater(claimed, actual * 0.7,
                           f"{claimed} against {actual} is stale enough to "
                           f"mislead")

    def test_the_card_count_is_too(self):
        claimed = self._claimed(DOCS["limitations.md"], r"over (\d+) checks")
        self.assertIsNotNone(claimed)
        actual = self._card_checks()
        self.assertLessEqual(claimed, actual)
        self.assertGreater(claimed, actual * 0.7)

    def test_the_commands_beside_them_are_the_real_ones(self):
        """`node --test` runs Node's own test runner, which this file does
        not use -- so the documented command found nothing."""
        self.assertIn("`node tests/test_cards.mjs`", DOCS["limitations.md"])
        self.assertNotIn("node --test tests/test_cards.mjs",
                         DOCS["limitations.md"])


class TheNotesDoNotContradictThemselves(unittest.TestCase):
    def test_the_doorbell_code_is_named_as_known_everywhere(self):
        """One section said 17 is a press, confirmed against a real one, and
        two others said which code means a press was still unknown."""
        for name in ("protocol-notes.md", "reference.md", "limitations.md"):
            text = DOCS[name]
            for claim in ("presses are not distinguishable",
                          "doorbell press is still unknown",
                          "press cannot be told"):
                with self.subTest(f"{name}: {claim}"):
                    live = [line for line in text.splitlines()
                            if claim in line and not line.lstrip().startswith("~~")
                            and "~~" not in line]
                    self.assertEqual(live, [])

    def test_what_the_code_actually_says_is_what_the_docs_say(self):
        source = (COMPONENT / "const.py").read_text()
        self.assertIn("RING_ALARM_TYPES: set[int] = {17}", source)
        self.assertIn("17: \"doorbell\"", source)
        self.assertIn("`alarm_type` 17", DOCS["reference.md"])


if __name__ == "__main__":
    unittest.main()
