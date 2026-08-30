#!/usr/bin/env python3
"""Line coverage for the dashboard card, with nothing installed.

The Python side has tools/coverage.py; the card had nothing, and "all card
tests passed" said only that the ones written pass. Node emits V8 coverage on
its own when NODE_V8_COVERAGE is set, so this costs no dependency either --
it runs the card suite, reads what V8 recorded, and turns byte ranges into
the lines they cover.

V8 reports ranges rather than lines, and a range that was never entered is
what marks the code inside it uncovered. So a line counts as covered when any
byte of it lies in a counted range and outside every zero-count one.

    tools/card_coverage.py          # the number
    tools/card_coverage.py --missing  # and the unexecuted lines
    tools/card_coverage.py --gate     # exit nonzero below the floor
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "custom_components" / "tapo_h500" / "www" / "tapo-h500-card.js"
FLOOR = 96.0


def _uncovered_ranges(entries) -> list[tuple[int, int]]:
    """Byte spans V8 says were never entered."""
    empty = []
    for entry in entries:
        for function in entry["functions"]:
            for span in function["ranges"]:
                if span["count"] == 0:
                    empty.append((span["startOffset"], span["endOffset"]))
    return empty


def _line_spans(source: str) -> list[tuple[int, int, int]]:
    """(line number, start byte, end byte) for every line with code on it."""
    spans, offset = [], 0
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        stripped = line.strip()
        # Blank lines and comment-only lines are not code. Nothing here tries
        # to find the end of a block comment; the card does not use them.
        if stripped and not stripped.startswith(("//", "*", "/*")):
            spans.append((number, offset, offset + len(line.rstrip("\n"))))
        offset += len(line.encode())
    return spans


def main() -> int:
    show_missing = "--missing" in sys.argv
    gate = "--gate" in sys.argv
    with tempfile.TemporaryDirectory() as into:
        environment = {**os.environ, "NODE_V8_COVERAGE": into}
        run = subprocess.run(["node", str(ROOT / "tests" / "test_cards.mjs")],
                             cwd=ROOT, env=environment,
                             capture_output=True, text=True)
        if run.returncode != 0:
            print(run.stdout or run.stderr)
            print("card suite failed; coverage says nothing about a red suite")
            return 1
        entries = []
        for path in Path(into).glob("*.json"):
            entries.extend(
                entry for entry in json.loads(path.read_text())["result"]
                if entry["url"].endswith("tapo-h500-card.js"))
    if not entries:
        print("no coverage recorded for the card")
        return 1

    empty = _uncovered_ranges(entries)
    source = CARD.read_text()
    missing = []
    for number, start, end in _line_spans(source):
        if any(low <= start and end <= high for low, high in empty):
            missing.append(number)
    total = len(_line_spans(source))
    covered = total - len(missing)
    percent = 100 * covered / total if total else 100
    print(f"{CARD.name}: {covered}/{total} lines  {percent:5.1f}%")
    if show_missing and missing:
        print(f"    missing: {_ranges(missing)}")
    if gate and percent < FLOOR:
        print(f"GATE: the card is {percent:.1f}% covered, floor {FLOOR:.0f}%")
        return 2
    if gate:
        print(f"card coverage OK ({percent:.1f}%, floor {FLOOR:.0f}%)")
    return 0


def _ranges(numbers: list[int]) -> str:
    """3,4,5,9 -> "3-5, 9". A wall of integers is not a report."""
    out, start, previous = [], numbers[0], numbers[0]
    for value in numbers[1:] + [None]:
        if value == previous + 1:
            previous = value
            continue
        out.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    return ", ".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
