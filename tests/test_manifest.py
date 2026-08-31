"""The manifest, checked here rather than discovered by a user's HACS.

hassfest is Home Assistant's own manifest validator and it runs in CI, where
it needs the network and a checkout. These are the same rules applied locally,
so a manifest mistake fails on the commit that made it rather than on somebody
else's install -- and so the suite still says something useful when CI is not
reachable.

Also pins the two facts nothing else was checking: that the version in the
manifest is the one the latest release commit claims, and that the pytapo
requirement stays a hard pin. That pin is deliberate -- pytapo here is a fork
whose media-session behaviour this integration depends on precisely, and a
range would let an unrelated release change what a download does.
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
MANIFEST = json.loads(
    (ROOT / "custom_components" / "tapo_h500" / "manifest.json").read_text())
HACS = json.loads((ROOT / "hacs.json").read_text())

# What hassfest requires of a custom integration, and why each one matters.
REQUIRED = {
    "domain": "the folder name and every entity's prefix",
    "name": "what the user sees in the integrations list",
    "version": "HACS refuses a custom integration without one",
    "documentation": "the Documentation link on the integration page",
    "codeowners": "hassfest requires the key even when it is empty",
    "iot_class": "declares that this polls locally rather than via a cloud",
}


class TheManifest(unittest.TestCase):
    def test_it_has_everything_hassfest_requires(self):
        for key, why in REQUIRED.items():
            with self.subTest(key):
                self.assertIn(key, MANIFEST, why)

    def test_the_domain_matches_the_folder(self):
        """They are the same string in Home Assistant's eyes, and a mismatch
        fails at load with a message that names neither."""
        self.assertEqual(MANIFEST["domain"],
                         (ROOT / "custom_components" / "tapo_h500").name)

    def test_the_version_is_a_plain_semver(self):
        """HACS sorts releases by this. A `v` prefix or a suffix sorts wrong,
        which shows up as "no update available" on an out-of-date install."""
        self.assertRegex(MANIFEST["version"], r"^\d+\.\d+\.\d+$")

    def test_the_iot_class_says_local_polling(self):
        """The whole premise of the integration. Changing it would be a claim
        that it talks to a cloud, which it is built never to do."""
        self.assertEqual(MANIFEST["iot_class"], "local_polling")

    def test_no_dependency_is_actually_a_pip_package(self):
        """`dependencies` are Home Assistant components; `requirements` are pip
        packages. Putting a pip name in the first fails at setup with a
        missing-integration error that names the package, not the mistake.

        Checked by cross-reference rather than by shape: `pytapo` is a valid
        component name as far as any regex can tell, so a pattern here passes
        the exact confusion it is meant to catch.
        """
        packages = {requirement.split("==")[0].split(">=")[0].strip().lower()
                    for requirement in MANIFEST["requirements"]}
        for name in MANIFEST["dependencies"]:
            with self.subTest(name):
                self.assertRegex(name, r"^[a-z_]+$")
                self.assertNotIn(
                    name.lower(), packages,
                    f"{name} is a pip requirement, not an HA component")

    def test_the_hub_requirement_stays_pinned(self):
        """Deliberately `==`, not `>=`.

        pytapo here is a fork whose media-session behaviour this integration
        depends on precisely -- the empty nonce, the session lifecycle, the
        error text the auth classifier reads. A range would let an unrelated
        release change what a download does on somebody's hub.
        """
        for requirement in MANIFEST["requirements"]:
            with self.subTest(requirement):
                self.assertIn("==", requirement,
                              "pin it, do not range it")
                self.assertRegex(requirement, r"^[A-Za-z0-9_.-]+==\d+\.\d+")

    def test_hacs_declares_the_home_assistant_floor(self):
        """Without it HACS offers the integration to versions that cannot run
        it, and the failure lands as a stack trace on the user."""
        self.assertRegex(HACS["homeassistant"], r"^\d{4}\.\d+\.\d+$")


class TheVersionMatchesTheHistory(unittest.TestCase):
    """The release commits say `release: X.Y.Z`; the manifest must agree.

    Nothing checked this, and the two drift silently: a feature committed
    after a release bump ships describing itself as the previous version, so
    a bug report names a release that does not contain the code in it.
    """

    @staticmethod
    def _latest_release_version():
        try:
            log = subprocess.run(
                ["git", "log", "--format=%s", "-n", "400"],
                capture_output=True, text=True, cwd=ROOT, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        for subject in log.splitlines():
            found = re.fullmatch(r"release: (\d+\.\d+\.\d+)", subject.strip())
            if found:
                return found.group(1)
        return None

    def test_the_manifest_is_at_or_ahead_of_the_last_release(self):
        released = self._latest_release_version()
        if released is None:
            self.skipTest("no release commit in reach, or no git")
        current = tuple(int(part) for part in MANIFEST["version"].split("."))
        self.assertGreaterEqual(
            current, tuple(int(part) for part in released.split(".")),
            f"manifest {MANIFEST['version']} is behind release {released}")



class TheOwner(unittest.TestCase):
    """Who to talk to.

    Home Assistant shows the code owner on the integration page, and an empty
    list reads as abandoned. It is also what the Silver integration-owner
    rule asks for.
    """

    def test_somebody_owns_this(self):
        self.assertTrue(MANIFEST["codeowners"])

    def test_every_owner_is_a_github_handle(self):
        """hassfest rejects a bare name, and a handle without the @ silently
        attributes the project to nobody."""
        for owner in MANIFEST["codeowners"]:
            with self.subTest(owner=owner):
                self.assertTrue(owner.startswith("@"), owner)
                self.assertGreater(len(owner), 1)



class TheLoggers(unittest.TestCase):
    """Which third-party loggers "Enable debug logging" should raise.

    Without this, somebody debugging a hub problem turns debug logging on and
    gets none of the library's output -- which for this integration is
    exactly where the interesting failures are, in the session handshake.
    """

    def test_the_hub_library_is_named(self):
        self.assertIn("loggers", MANIFEST)
        self.assertIn("pytapo", MANIFEST["loggers"])

    def test_every_logger_belongs_to_something_installed(self):
        """A name nothing provides raises nothing and warns about nothing."""
        packages = {requirement.split("==")[0].split(">=")[0].strip()
                    for requirement in MANIFEST["requirements"]}
        for name in MANIFEST["loggers"]:
            with self.subTest(logger=name):
                self.assertIn(name.split(".")[0], packages)

    def test_our_own_logger_is_not_listed(self):
        """Home Assistant raises the integration's own logger by itself, and
        listing it is a hassfest warning."""
        self.assertNotIn(MANIFEST["domain"], MANIFEST["loggers"])



class TheKeysAreInTheOrderHassfestWants(unittest.TestCase):
    """domain and name first, then the rest alphabetically.

    hassfest fails on any other order, and it is the one check that does not
    run locally -- so a tidy-up that sorts the whole file goes green here and
    red on the push.
    """

    def test_domain_and_name_lead(self):
        self.assertEqual(list(MANIFEST)[:2], ["domain", "name"])

    def test_the_rest_are_alphabetical(self):
        rest = list(MANIFEST)[2:]
        self.assertEqual(rest, sorted(rest))


if __name__ == "__main__":
    unittest.main()
