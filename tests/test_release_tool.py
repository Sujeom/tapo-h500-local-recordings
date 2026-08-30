"""The release publisher turns tag annotations into Releases faithfully."""
import importlib.util
import subprocess
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "publish_releases", Path(__file__).parents[1] / "tools" /
    "publish-releases.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class RemoteParsing(unittest.TestCase):
    def test_ssh_and_https_remotes_both_parse(self):
        for url in ("git@github.com:Someone/some-repo.git",
                    "https://github.com/Someone/some-repo.git",
                    "https://github.com/Someone/some-repo"):
            self.assertEqual(tool.repo_from_remote(url), "Someone/some-repo")

    def test_a_non_github_remote_refuses(self):
        self.assertIsNone(tool.repo_from_remote(
            "ssh://git@192.168.4.60:2424/repos/misc/thing.git"))


class Planning(unittest.TestCase):
    TAGS = [("v0.1.0", "First", "body"), ("v0.2.0", "Second", "")]

    def test_existing_releases_are_skipped(self):
        self.assertEqual(tool.plan(self.TAGS, {"v0.1.0"}),
                         [("v0.2.0", "Second", "")])

    def test_nothing_missing_means_nothing_planned(self):
        self.assertEqual(tool.plan(self.TAGS, {"v0.1.0", "v0.2.0"}), [])


def _local_tags():
    """This repository's tags, or None where there are none to read.

    A checkout without them is a real situation and not a failure: a source
    tarball has no `.git` at all, and `actions/checkout` fetches no tags
    unless it is asked to. Erroring there says the release tool is broken
    when what is missing is the input.
    """
    try:
        tags = tool.local_tags()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return tags or None


class TagReading(unittest.TestCase):
    def test_real_tags_read_with_title_and_body(self):
        """Against this repository's actual tags: every annotated tag yields
        a name and a title, and the recent ones carry bodies.

        Skipped where there are no tags to read. CI asks for them -- a run
        that quietly skipped this would be a run that checked nothing -- and
        `tests/test_gate.py` holds the workflow to asking.
        """
        tags = _local_tags()
        if tags is None:
            self.skipTest("no tags in this checkout, so nothing to read")
        names = [name for name, _, _ in tags]
        self.assertIn("v0.81.0", names)
        by_name = {name: (title, body) for name, title, body in tags}
        title, body = by_name["v0.81.0"]
        self.assertEqual(title, "Media session lifecycle")
        self.assertIn("finished notification", body)


if __name__ == "__main__":
    unittest.main()
