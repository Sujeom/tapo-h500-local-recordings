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


class TheArchitectureMapIsCurrent(unittest.TestCase):
    """A map that drifts is worse than no map.

    Every module appears, described by its own first docstring line, and
    nothing appears that is gone. Line counts go stale by a line or two and
    that is fine; being out by a third is not.
    """

    MAP = (ROOT / "docs" / "architecture.md").read_text()
    MODULES = sorted(COMPONENT.glob("*.py"))

    @staticmethod
    def _summary(path):
        import ast
        doc = ast.get_docstring(ast.parse(path.read_text())) or ""
        return doc.splitlines()[0] if doc else ""

    def _rows(self):
        return {name: (int(lines), summary.strip()) for name, lines, summary
                in re.findall(r"\| `([\w.]+)` \| (\d+) \| ([^|]+) \|", self.MAP)}

    def test_every_module_is_on_the_map(self):
        missing = [p.name for p in self.MODULES if p.name not in self._rows()]
        self.assertEqual(missing, [])

    def test_nothing_on_the_map_has_been_deleted(self):
        real = {p.name for p in self.MODULES}
        self.assertEqual(sorted(set(self._rows()) - real), [])

    def test_each_is_described_in_its_own_words(self):
        """Copied from the module's docstring, so the two cannot disagree
        about what a file is for."""
        rows = self._rows()
        wrong = []
        for path in self.MODULES:
            claimed = rows[path.name][1]
            actual = self._summary(path)
            if claimed != actual:
                wrong.append(f"{path.name}: {claimed!r} != {actual!r}")
        self.assertEqual(wrong, [])

    def test_the_sizes_are_not_wildly_out(self):
        rows = self._rows()
        stale = []
        for path in self.MODULES:
            claimed = rows[path.name][0]
            actual = len(path.read_text().splitlines())
            if not 0.7 * actual <= claimed <= 1.3 * actual:
                stale.append(f"{path.name}: says {claimed}, is {actual}")
        self.assertEqual(stale, [])

    def test_the_readme_points_at_it(self):
        """A map nobody is told about is a map nobody reads."""
        self.assertIn("docs/architecture.md", DOCS["README.md"])

    def test_it_covers_the_card_too(self):
        """1,500 lines of JavaScript is a module by any measure."""
        self.assertIn("tapo-h500-card.js", self.MAP)



class TheChangelog(unittest.TestCase):
    """Generated from the tag annotations, never hand-kept.

    A hand-written changelog drifts within three releases, and the notes
    already exist: writing them a second time by hand is how they end up
    disagreeing with the Releases they describe.
    """

    PATH = ROOT / "CHANGELOG.md"

    def _entries(self):
        return [line for line in self.PATH.read_text().splitlines()
                if line.startswith("## ")]

    def test_it_exists(self):
        self.assertTrue(self.PATH.is_file())

    def test_the_newest_entry_is_the_version_that_ships(self):
        """Somebody reading it should see the version they are about to
        install, not the one before."""
        manifest = json.loads(
            (ROOT / "custom_components" / "tapo_h500" / "manifest.json")
            .read_text())
        self.assertTrue(
            self._entries()[0].startswith(f"## v{manifest['version']}"),
            f"newest entry is {self._entries()[0]!r}")

    def test_there_is_an_entry_per_version_tag(self):
        """The count is what catches a generator that stopped at a page
        boundary, which is the failure nobody reads far enough to notice."""
        try:
            listed = subprocess.run(
                ["git", "tag"], cwd=ROOT, capture_output=True, text=True,
                check=True).stdout.split()
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("no tags in this checkout")
        versions = [name for name in listed
                    if re.match(r"^v\d+\.\d+\.\d+", name)]
        if not versions:
            self.skipTest("no version tags in this checkout")
        self.assertEqual(len(self._entries()), len(versions))

    def test_it_says_where_it_came_from(self):
        """So the next person regenerates it rather than editing it."""
        self.assertIn("publish-releases.py --changelog",
                      self.PATH.read_text())



class TheRepositoryTellsPeopleHowToUseIt(unittest.TestCase):
    """Issue templates, a security policy and contributing notes.

    Every bug report that arrives without the firmware version is a round
    trip, and firmware is usually the answer -- the integration was reverse
    engineered against one.
    """

    TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"

    def test_the_bug_template_asks_for_what_is_always_needed(self):
        import yaml
        form = yaml.safe_load((self.TEMPLATES / "bug.yml").read_text())
        required = {field["id"] for field in form["body"]
                    if field.get("validations", {}).get("required")}
        for field in ("ha", "version", "firmware", "cameras", "diagnostics"):
            with self.subTest(field=field):
                self.assertIn(field, required)

    def test_blank_issues_are_off(self):
        """A blank issue arrives without a firmware version."""
        import yaml
        config = yaml.safe_load((self.TEMPLATES / "config.yml").read_text())
        self.assertFalse(config["blank_issues_enabled"])

    def test_the_no_live_view_answer_is_linked_before_filing(self):
        """It will otherwise be the most reported thing that is not a bug."""
        import yaml
        config = yaml.safe_load((self.TEMPLATES / "config.yml").read_text())
        self.assertTrue(any("limitations" in link["url"]
                            for link in config["contact_links"]))

    def test_contributing_names_the_gate(self):
        """The one instruction that must never go missing."""
        self.assertIn("tools/verify.sh",
                      (ROOT / "CONTRIBUTING.md").read_text())

    def test_contributing_warns_about_the_hardware(self):
        """Somebody who does not know the hub wedges will write a test that
        talks to one."""
        text = (ROOT / "CONTRIBUTING.md").read_text().lower()
        self.assertIn("wedges", text)
        self.assertIn("no test may talk to real hardware", text)

    def test_security_says_where_the_credentials_live(self):
        text = (ROOT / "SECURITY.md").read_text()
        self.assertIn("config entry", text)
        self.assertIn("allow-list", text)



class EveryLinkGoesSomewhere(unittest.TestCase):
    """Relative links in the docs, resolved against the repository.

    Documentation gets moved and renamed, and a dead link is invisible to
    everybody except the person following it -- who is by then already stuck
    on something else.
    """

    LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)")

    def _relative_links(self):
        for name in sorted(DOCS):
            source = ROOT / name if (ROOT / name).is_file() else ROOT / "docs" / name
            for target in self.LINK.findall(DOCS[name]):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                yield name, target, (source.parent / target).resolve()

    def test_they_all_resolve(self):
        broken = [f"{name} -> {target}"
                  for name, target, path in self._relative_links()
                  if not path.exists()]
        self.assertEqual(broken, [])

    def test_there_are_some_to_check(self):
        """A regex that matched nothing would make the check above pass on an
        empty list forever."""
        self.assertGreater(len(list(self._relative_links())), 5)


class TheRepositoryRootIsTheFrontDoor(unittest.TestCase):
    """What somebody sees before they have read anything.

    Working notes belong in docs/ or out of the repository entirely; the root
    is where people decide what a project is.
    """

    ALLOWED = {"README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md"}

    def test_only_the_documents_meant_for_a_stranger_are_there(self):
        try:
            tracked = subprocess.run(
                ["git", "ls-files"], cwd=ROOT, capture_output=True,
                text=True, check=True).stdout.split()
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("no git in this checkout")
        at_root = {name for name in tracked
                   if "/" not in name and name.endswith(".md")}
        self.assertEqual(at_root - self.ALLOWED, set())



class TheTroubleshootingPage(unittest.TestCase):
    """Written from what the integration actually reports.

    Every repair notice and entity it names has to exist -- documentation
    that names something renamed is worse than none, because somebody
    follows it while already stuck on something else.
    """

    PATH = ROOT / "docs" / "troubleshooting.md"
    TEXT = property(lambda self: self.PATH.read_text())

    def test_it_exists(self):
        self.assertTrue(self.PATH.is_file())

    def test_every_repair_notice_it_names_is_a_real_one(self):
        """Named in prose rather than by key, so this matches the wording
        the notice itself uses."""
        import json
        issues = json.loads(
            (ROOT / "custom_components" / "tapo_h500" / "strings.json")
            .read_text())["issues"]
        # A title with a placeholder is written in the docs with an ellipsis
        # where the value goes, so both halves have to match something.
        titles = [body["title"] for body in issues.values()]
        quoted = re.findall(r'repair notice \*\*"([^"]+)"\*\*', self.TEXT)
        quoted += re.findall(r'\*\*"([^"]+)"\*\* appears', self.TEXT)
        self.assertTrue(quoted, "it should point at some")
        for phrase in quoted:
            with self.subTest(notice=phrase):
                # Markdown wraps, so a quoted title can span two lines.
                phrase = " ".join(phrase.split())
                halves = [part.strip() for part in phrase.split("…")]
                self.assertTrue(
                    any(all(half in title for half in halves)
                        for title in titles),
                    f"no repair notice reads {phrase!r}")

    def test_it_covers_the_failures_this_hub_actually_has(self):
        """Each of these cost somebody a day at some point, and each has an
        entity that already knows the answer."""
        for symptom in ("wedge", "silent", "clock", "Custom element",
                        "share a folder", "Empty recordings"):
            with self.subTest(symptom=symptom):
                self.assertIn(symptom, self.TEXT)

    def test_it_says_the_wedge_recovers_on_a_timeout(self):
        """The single most useful sentence in the file: everything anybody
        instinctively tries makes it worse."""
        self.assertIn("recovers on a timeout", self.TEXT)

    def test_it_tells_people_how_to_get_diagnostics(self):
        self.assertIn("Download diagnostics", self.TEXT)
        self.assertIn("allow-list", self.TEXT)

    def test_the_readme_and_the_issue_template_point_at_it(self):
        """An unlinked troubleshooting page is not a troubleshooting page."""
        self.assertIn("troubleshooting.md", DOCS["README.md"])


if __name__ == "__main__":
    unittest.main()
