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


class WhichRepository(unittest.TestCase):
    """Where the Releases go, answered the same way on a laptop and in CI.

    The tool asked git for a remote named `github`, which is what the
    owner's clone calls it. actions/checkout names its one remote `origin`,
    so the release workflow's very first run -- the v0.124.0 tag -- stopped
    at the dry run with "No GitHub remote named 'github'", published
    nothing and never refreshed the changelog, which is what left the
    release commit red in the verify workflow.
    """

    def _resolve(self, env=None, remotes=None):
        from unittest import mock
        remotes = remotes or {}
        with mock.patch.dict(tool.os.environ, env or {}, clear=True), \
                mock.patch.object(tool, "remote_url",
                                  lambda name: remotes.get(name, "")):
            return tool.repository()

    def test_actions_says_which_repository_it_is_running_in(self):
        """GITHUB_REPOSITORY is set on every run, whatever the remote is
        called, and a fork's checkout should publish to the fork."""
        self.assertEqual(
            self._resolve(env={"GITHUB_REPOSITORY": "Someone/fork"},
                          remotes={"origin": "https://github.com/Else/x.git"}),
            "Someone/fork")

    def test_a_clone_with_a_github_remote_still_works(self):
        self.assertEqual(
            self._resolve(remotes={
                "origin": "ssh://git@192.168.4.60:2424/repos/misc/thing.git",
                "github": "git@github.com:Someone/some-repo.git"}),
            "Someone/some-repo")

    def test_a_plain_checkout_whose_origin_is_github_works_too(self):
        self.assertEqual(
            self._resolve(remotes={
                "origin": "https://github.com/Someone/some-repo"}),
            "Someone/some-repo")

    def test_no_github_remote_anywhere_is_still_a_refusal(self):
        self.assertIsNone(self._resolve(remotes={
            "origin": "ssh://git@192.168.4.60:2424/repos/misc/thing.git"}))


class WhichTagsAreVersions(unittest.TestCase):
    """Not every tag is a release.

    This repository also carries backup/ refs pointing at branch tips.
    Publishing one would offer HACS a version called backup/origin-main to
    install, and the dry run before the first backfill is where that was
    caught.
    """

    def test_a_version_tag_is_one(self):
        for name in ("v0.5.0", "v1.0.0", "v0.123.0", "v2.10.3"):
            with self.subTest(tag=name):
                self.assertTrue(tool.VERSION_TAG.match(name), name)

    def test_a_branch_backup_is_not(self):
        for name in ("backup/origin-main", "backup/local-main",
                     "backup/feat-branch"):
            with self.subTest(tag=name):
                self.assertIsNone(tool.VERSION_TAG.match(name), name)

    def test_nor_is_anything_that_merely_starts_with_v(self):
        for name in ("vendor-sync", "v", "version-2", "v1.2"):
            with self.subTest(tag=name):
                self.assertIsNone(tool.VERSION_TAG.match(name), name)


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

    def test_no_backup_ref_reaches_the_plan(self):
        """Against the real tag list, so the exclusion is proven on the refs
        that actually exist rather than on invented names."""
        tags = _local_tags()
        if tags is None:
            self.skipTest("no tags in this checkout, so nothing to read")
        names = [name for name, _, _ in tags]
        listed = subprocess.run(
            ["git", "tag"], cwd=Path(__file__).parents[1],
            capture_output=True, text=True, check=True).stdout.split()
        excluded = [name for name in listed if not name.startswith("v")]
        if not excluded:
            self.skipTest("no non-version tag to prove exclusion with")
        for name in excluded:
            self.assertNotIn(name, names)


if __name__ == "__main__":
    unittest.main()
