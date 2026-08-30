#!/usr/bin/env python3
"""Create a GitHub Release for every annotated tag that lacks one.

The repository has had a tag per release since v0.5.0 and no Release
objects, so HACS shows no changelog and no release notes accompany an
update. Each tag's own annotation is already the release note -- this
publishes it, verbatim: the annotation's first line becomes the release
title and the rest its body.

Needs a GitHub token with `contents: write` on the repository:

    GITHUB_TOKEN=ghp_... tools/publish-releases.py
    tools/publish-releases.py --dry-run     # no token needed
    tools/publish-releases.py --changelog > CHANGELOG.md

Idempotent: tags that already have a Release are skipped, so running it
after every few tags is fine. Stdlib only.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

API = "https://api.github.com"


def repo_from_remote(url: str) -> str | None:
    """"owner/name" out of an SSH or HTTPS GitHub remote."""
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url.strip())
    return match.group(1) if match else None


# Releases are versions. Not every tag is one: this repository also carries
# backup/ refs pointing at branch tips, and publishing those as Releases would
# offer HACS a "version" called backup/origin-main to install.
VERSION_TAG = re.compile(r"^v\d+\.\d+\.\d+")


def local_tags() -> list[tuple[str, str, str]]:
    """(tag, title, body) per annotated version tag, oldest first."""
    out = subprocess.run(
        ["git", "for-each-ref", "refs/tags", "--sort=creatordate",
         "--format=%(refname:short)%00%(contents)%01"],
        capture_output=True, text=True, check=True).stdout
    tags = []
    for block in out.split("\x01"):
        if "\x00" not in block:
            continue
        name, contents = block.split("\x00", 1)
        name = name.strip()
        if not VERSION_TAG.match(name):
            continue
        lines = contents.strip().splitlines() or [name]
        tags.append((name, lines[0].strip(),
                     "\n".join(lines[1:]).strip()))
    return tags


def plan(tags: list[tuple[str, str, str]],
         existing: set[str]) -> list[tuple[str, str, str]]:
    return [entry for entry in tags if entry[0] not in existing]


def _request(url: str, token: str, payload: dict | None = None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "tapo-h500-release-tool"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(request) as reply:
        return json.loads(reply.read())


def existing_releases(repo: str, token: str) -> set[str]:
    found: set[str] = set()
    page = 1
    while True:
        batch = _request(
            f"{API}/repos/{repo}/releases?per_page=100&page={page}", token)
        if not batch:
            return found
        found.update(release.get("tag_name", "") for release in batch)
        page += 1


def changelog(tags: list[tuple[str, str, str]]) -> str:
    """The same annotations as one document, newest first.

    Generated rather than written. A hand-kept changelog drifts within three
    releases, and the notes already exist -- writing them a second time by
    hand is how they end up disagreeing with the Releases they describe.
    """
    dates = dict(
        line.split("\x00", 1)
        for line in subprocess.run(
            ["git", "for-each-ref", "refs/tags",
             "--format=%(refname:short)%00%(creatordate:short)"],
            capture_output=True, text=True, check=True).stdout.splitlines()
        if "\x00" in line)
    out = ["# Changelog", "",
           "Generated from the tag annotations by "
           "`tools/publish-releases.py --changelog`. Every entry is the note "
           "written when that version was tagged.", ""]
    for name, title, body in reversed(tags):
        when = dates.get(name, "")
        out.append(f"## {name}" + (f" &mdash; {when}" if when else ""))
        out.append("")
        out.append(title)
        if body:
            out.extend(["", body])
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    dry = "--dry-run" in sys.argv
    tags = local_tags()
    if "--changelog" in sys.argv:
        # No remote needed: this reads tags and writes a file.
        sys.stdout.write(changelog(tags))
        return 0
    url = subprocess.run(["git", "remote", "get-url", "github"],
                         capture_output=True, text=True).stdout
    repo = repo_from_remote(url)
    if repo is None:
        print("No GitHub remote named 'github' to publish to")
        return 2
    if dry:
        for name, title, _ in tags:
            print(f"would ensure {name}: {title}")
        print(f"{len(tags)} tags; releases skipped without a token")
        return 0
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("Set GITHUB_TOKEN (contents: write), or use --dry-run")
        return 2
    missing = plan(tags, existing_releases(repo, token))
    for name, title, body in missing:
        _request(f"{API}/repos/{repo}/releases", token, {
            "tag_name": name, "name": title, "body": body})
        print(f"created {name}: {title}")
    print(f"{len(missing)} created, {len(tags) - len(missing)} already there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
