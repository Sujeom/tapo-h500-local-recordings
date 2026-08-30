"""The workflow that turns a tag into a Release.

With no Releases at all, HACS installs the default branch: everybody gets
whatever was on main when they pressed update, with no version to pin to and
no notes. 126 version tags carried their own notes in their annotations and
none had ever been published.

The tool itself is tested in test_release_tool.py; this is about what runs it.
"""
import unittest
from pathlib import Path

import yaml

WORKFLOW = (Path(__file__).parents[1] / ".github" / "workflows"
            / "release.yml")


class TheWorkflow(unittest.TestCase):
    def setUp(self):
        import yaml
        self.doc = yaml.safe_load(WORKFLOW.read_text())
        # "on" is YAML's boolean true unless quoted, which is a trap worth
        # asserting around rather than tripping over.
        self.triggers = self.doc.get("on", self.doc.get(True))

    def test_it_fires_on_a_version_tag(self):
        self.assertIn("v*", self.triggers["push"]["tags"])

    def test_it_can_also_be_run_by_hand(self):
        """This is what makes the backfill possible without anybody issuing a
        personal token: the tool is idempotent, so a manual run publishes
        every tag that has no Release yet."""
        self.assertIn("workflow_dispatch", self.triggers)

    def test_it_may_write_releases_and_nothing_else(self):
        self.assertEqual(self.doc["permissions"], {"contents": "write"})

    def test_it_checks_out_the_annotations(self):
        """The annotation is the release note. A shallow checkout has
        neither the tags nor their messages."""
        steps = self.doc["jobs"]["publish"]["steps"]
        checkout = next(s for s in steps if "checkout" in str(s.get("uses")))
        self.assertEqual(checkout["with"]["fetch-depth"], 0)

    def test_it_shows_the_plan_before_publishing(self):
        """A backfill of a hundred-odd releases should print what it is about
        to do while somebody can still stop it."""
        steps = self.doc["jobs"]["publish"]["steps"]
        runs = [s.get("run", "") for s in steps]
        dry = next(i for i, r in enumerate(runs) if "--dry-run" in r)
        live = next(i for i, r in enumerate(runs)
                    if "publish-releases.py" in r and "--dry-run" not in r)
        self.assertLess(dry, live)


if __name__ == "__main__":
    unittest.main()
