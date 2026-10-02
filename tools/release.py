#!/usr/bin/env python3
"""Cut a release in one commit that passes its own gate.

    tools/release.py 0.125.0 notes.txt

notes.txt is the release note: the first line is the title, the rest the
body. The same text becomes the tag annotation, which the release workflow
publishes as the GitHub Release and regenerates CHANGELOG.md from.

The changelog entry is written here, before the commit, dated today -- the
same entry the generator produces once the tag exists. The changelog tests
pin the newest entry to the manifest version, so a release commit made
before its tag was red in CI until the workflow's regenerated changelog
landed a commit later. Written up front, the refresh finds nothing to do.

In order: refuse a dirty tree, a malformed or non-increasing version, or an
existing tag; bump manifest.json; rewrite CHANGELOG.md; commit
"release: X.Y.Z"; tag vX.Y.Z with the notes; push the tag to the GitHub
remote. A post-commit hook, where there is one, pushes the commit; the tag
push is what triggers the release workflow.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "tapo_h500" / "manifest.json"
CHANGELOG = ROOT / "CHANGELOG.md"

# The generator itself, so the entry written here and the one regenerated
# later come from one function and cannot drift.
_SPEC = importlib.util.spec_from_file_location(
    "publish_releases", ROOT / "tools" / "publish-releases.py")
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)

VERSION = re.compile(r"^\d+\.\d+\.\d+$")
VERSION_LINE = re.compile(r'("version":\s*")(\d+\.\d+\.\d+)(")')


def check_version(version: str, current: str, tags: list[str]) -> str | None:
    """Why this version cannot be released, or None when it can."""
    if not VERSION.match(version):
        return f"{version!r} is not MAJOR.MINOR.PATCH"
    # As numbers: as strings 0.9.0 sorts after 0.124.0.
    if (tuple(int(part) for part in version.split("."))
            <= tuple(int(part) for part in current.split("."))):
        return f"{version} is not after the current {current}"
    if f"v{version}" in tags:
        return f"tag v{version} already exists"
    return None


def bump_manifest(text: str, version: str) -> str:
    """The manifest with only its version line changed. hassfest cares about
    key order, so this is a substitution, not a JSON round trip."""
    bumped, count = VERSION_LINE.subn(rf"\g<1>{version}\g<3>", text, count=1)
    if count != 1:
        raise ValueError("no version line in manifest.json")
    return bumped


def split_notes(text: str) -> tuple[str, str]:
    """(title, body): the first line, and the rest."""
    lines = text.strip().splitlines()
    if not lines or not lines[0].strip():
        raise ValueError("the notes need a title on their first line")
    return lines[0].strip(), "\n".join(lines[1:]).strip()


def pending_changelog(version: str, notes: str, today: str) -> str:
    """CHANGELOG.md as the generator will render it once vVERSION exists."""
    title, body = split_notes(notes)
    name = f"v{version}"
    return tool.changelog(tool.local_tags() + [(name, title, body)],
                          {**tool.tag_dates(), name: today})


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    version, notes_path = argv[1], Path(argv[2])
    if _git("status", "--porcelain", "--untracked-files=no").strip():
        print("the working tree has uncommitted changes; commit or stash first")
        return 2
    current = VERSION_LINE.search(MANIFEST.read_text()).group(2)
    reason = check_version(version, current, _git("tag").split())
    if reason:
        print(reason)
        return 2
    notes = notes_path.read_text()
    rendered = pending_changelog(version, notes, date.today().isoformat())
    MANIFEST.write_text(bump_manifest(MANIFEST.read_text(), version))
    CHANGELOG.write_text(rendered)
    _git("add", str(MANIFEST), str(CHANGELOG))
    _git("commit", "-q", "-m", f"release: {version}")
    _git("tag", "-a", f"v{version}", "-F", str(notes_path))
    remote = "github" if tool.remote_url("github").strip() else "origin"
    _git("push", remote, f"v{version}")
    print(f"released v{version}; the tag push to {remote} runs the release "
          "workflow, which publishes it and finds the changelog current")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
