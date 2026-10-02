"""Cutting a release in one commit that passes its own gate.

The changelog is generated from tag annotations, and the changelog tests pin
its newest entry to the manifest version -- so a release commit made before
its tag was always red in CI until the workflow's regenerated changelog
landed a commit later. tools/release.py writes that entry before the commit,
in the shape the generator will produce once the tag exists, so the refresh
afterwards finds nothing to change.

The git plumbing at the end of the tool is four subprocess calls and is not
exercised here; everything it decides beforehand is.
"""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_cut", ROOT / "tools" / "release.py")
cut = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cut)

MANIFEST = '''{
  "domain": "tapo_h500",
  "name": "Tapo H500",
  "version": "0.124.0",
  "requirements": ["pytapo==3.4.18"]
}
'''


class TheVersionIsChecked(unittest.TestCase):
    TAGS = ["v0.123.0", "v0.124.0", "backup/origin-main"]

    def test_the_next_version_is_allowed(self):
        self.assertIsNone(cut.check_version("0.125.0", "0.124.0", self.TAGS))
        self.assertIsNone(cut.check_version("1.0.0", "0.124.0", self.TAGS))

    def test_a_malformed_version_is_refused(self):
        for bad in ("0.125", "v0.125.0", "0.125.0-rc1", "latest"):
            with self.subTest(version=bad):
                self.assertIsNotNone(
                    cut.check_version(bad, "0.124.0", self.TAGS))

    def test_going_backwards_or_sideways_is_refused(self):
        """0.9.0 after 0.124.0 is the comparison a string compare gets
        wrong, so it is the one pinned."""
        for bad in ("0.124.0", "0.123.0", "0.9.0"):
            with self.subTest(version=bad):
                self.assertIsNotNone(
                    cut.check_version(bad, "0.124.0", self.TAGS))

    def test_an_existing_tag_is_refused(self):
        self.assertIn("already", cut.check_version(
            "0.125.0", "0.124.0", self.TAGS + ["v0.125.0"]))


class TheManifestIsBumped(unittest.TestCase):
    def test_only_the_version_line_changes(self):
        bumped = cut.bump_manifest(MANIFEST, "0.125.0")
        self.assertEqual(bumped, MANIFEST.replace("0.124.0", "0.125.0"))

    def test_a_manifest_without_a_version_line_refuses(self):
        with self.assertRaises(ValueError):
            cut.bump_manifest('{"domain": "tapo_h500"}\n', "0.125.0")


class TheNotes(unittest.TestCase):
    def test_first_line_is_the_title_and_the_rest_the_body(self):
        self.assertEqual(
            cut.split_notes("A title\n\nFirst paragraph.\n\nSecond.\n"),
            ("A title", "First paragraph.\n\nSecond."))

    def test_a_title_alone_has_no_body(self):
        self.assertEqual(cut.split_notes("Just a title\n"),
                         ("Just a title", ""))

    def test_empty_notes_are_refused(self):
        with self.assertRaises(ValueError):
            cut.split_notes("\n\n")


class TheChangelogIsWrittenUpFront(unittest.TestCase):
    """Rendered through the real generator with the pending tag added, dated
    today: byte for byte what `publish-releases.py --changelog` produces
    once the tag exists, so the workflow's refresh is a no-op."""

    def _render(self):
        with mock.patch.object(cut.tool, "local_tags",
                               lambda: [("v0.1.0", "First", "body one")]), \
                mock.patch.object(cut.tool, "tag_dates",
                                  lambda: {"v0.1.0": "2026-01-01"}):
            return cut.pending_changelog(
                "0.2.0", "Second\n\nbody two\n", "2026-10-02")

    def test_the_pending_entry_leads_with_todays_date(self):
        text = self._render()
        entries = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(entries, ["## v0.2.0 &mdash; 2026-10-02",
                                   "## v0.1.0 &mdash; 2026-01-01"])

    def test_it_is_the_generators_own_output(self):
        """Not a hand-made entry glued on top: the same function, the same
        header, so the two can never drift."""
        with mock.patch.object(cut.tool, "tag_dates",
                               lambda: {"v0.1.0": "2026-01-01",
                                        "v0.2.0": "2026-10-02"}):
            expected = cut.tool.changelog(
                [("v0.1.0", "First", "body one"),
                 ("v0.2.0", "Second", "body two")])
        self.assertEqual(self._render(), expected)


if __name__ == "__main__":
    unittest.main()
