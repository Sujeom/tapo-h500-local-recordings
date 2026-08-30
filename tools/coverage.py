#!/usr/bin/env python3
"""Line coverage for the component, with nothing installed.

coverage.py is not here, and this suite deliberately has no third-party test
dependency -- stdlib unittest, plus yaml, jinja2 and node. `sys.monitoring`
answers the one question worth asking without adding one: which lines of the
component does the suite actually execute?

Not a replacement for coverage.py. No branch coverage, no HTML, no exclusion
pragmas. What it gives is the number that was missing entirely, per module,
so "which file should get the next behavioural test" stops being a guess.

    tools/coverage.py            # summary, worst first
    tools/coverage.py --missing  # and the unexecuted line numbers
    tools/coverage.py --gate     # exit nonzero below the floors
"""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "tapo_h500"
TOOL_ID = sys.monitoring.PROFILER_ID


def executable_lines(path: Path) -> set[int]:
    """Every line the compiler emitted code for.

    Walks nested code objects: a function body's lines live on its own code
    object, so counting only the module's would call a file of functions
    fully covered the moment it imported.
    """
    code = compile(path.read_text(), str(path), "exec")
    lines: set[int] = set()
    pending = [code]
    while pending:
        current = pending.pop()
        for _start, _end, lineno in current.co_lines():
            if lineno:
                lines.add(lineno)
        pending.extend(const for const in current.co_consts
                       if isinstance(const, types.CodeType))
    return lines


def main() -> int:
    show_missing = "--missing" in sys.argv
    gate = "--gate" in sys.argv
    files = {str(path): path for path in sorted(COMPONENT.glob("*.py"))}
    seen: dict[str, set[int]] = {name: set() for name in files}

    def on_line(code, lineno):
        record = seen.get(code.co_filename)
        if record is None:
            # Not ours: turn this location off for good rather than pay for it
            # on every execution. This is what keeps the run near full speed.
            return sys.monitoring.DISABLE
        record.add(lineno)
        return sys.monitoring.DISABLE

    sys.path.insert(0, str(ROOT / "tests"))
    sys.monitoring.use_tool_id(TOOL_ID, "tapo-h500-coverage")
    sys.monitoring.register_callback(
        TOOL_ID, sys.monitoring.events.LINE, on_line)
    sys.monitoring.set_events(TOOL_ID, sys.monitoring.events.LINE)
    try:
        suite = unittest.TestLoader().discover(
            str(ROOT / "tests"), pattern="test_*.py")
        result = unittest.TextTestRunner(
            verbosity=0, stream=open("/dev/null", "w")).run(suite)
    finally:
        sys.monitoring.set_events(TOOL_ID, 0)
        sys.monitoring.free_tool_id(TOOL_ID)

    rows = []
    for name, path in files.items():
        runnable = executable_lines(path)
        # Only lines the compiler emitted count as missable; a line seen but
        # not in `runnable` is a decorator or a continuation and would make
        # the percentage exceed 100.
        covered = seen[name] & runnable
        rows.append((path.name, len(covered), len(runnable),
                     sorted(runnable - covered)))
    rows.sort(key=lambda row: (row[1] / row[2] if row[2] else 1, -row[2]))

    total_covered = sum(row[1] for row in rows)
    total_runnable = sum(row[2] for row in rows)
    print(f"{'module':26s} {'covered':>9s} {'lines':>7s}  percent")
    print("-" * 55)
    for name, covered, runnable, missing in rows:
        pct = 100 * covered / runnable if runnable else 100
        print(f"{name:26s} {covered:9d} {runnable:7d}  {pct:5.1f}%")
        if show_missing and missing:
            print(f"    missing: {_ranges(missing)}")
    print("-" * 55)
    print(f"{'TOTAL':26s} {total_covered:9d} {total_runnable:7d}  "
          f"{100 * total_covered / total_runnable:5.1f}%")
    print(f"\n{result.testsRun} tests ran; "
          f"{len(result.failures)} failed, {len(result.errors)} errored")
    if not result.wasSuccessful():
        return 1
    if gate:
        return _gate(rows, 100 * total_covered / total_runnable)
    return 0


# The floors --gate holds. Ratchets, not targets: TOTAL sits just under where
# the suite actually is, so improvement is kept rather than demanded, and the
# per-module floor exists to make a NEW untested module fail the build -- nine
# shipped at 0.0% and nothing said so until somebody went looking.
FLOOR_TOTAL = 75.0
FLOOR_MODULE = 20.0


def _gate(rows: list, total: float) -> int:
    failed = False
    for name, covered, runnable, _missing in sorted(rows):
        pct = 100 * covered / runnable if runnable else 100.0
        if pct < FLOOR_MODULE:
            print(f"GATE: {name} is {pct:.1f}% covered, "
                  f"floor {FLOOR_MODULE:.0f}%")
            failed = True
    if total < FLOOR_TOTAL:
        print(f"GATE: total {total:.1f}% is under the floor "
              f"{FLOOR_TOTAL:.0f}%")
        failed = True
    if failed:
        return 2
    print(f"coverage gate OK (total {total:.1f}%, floors "
          f"{FLOOR_TOTAL:.0f}/{FLOOR_MODULE:.0f})")
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
