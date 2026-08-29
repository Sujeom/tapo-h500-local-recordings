#!/usr/bin/env python3
"""Mutation testing for the paths where a wrong answer loses footage.

A passing suite says the tests agree with the code. It does not say the tests
would notice if the code were wrong -- and that gap has been expensive twice
here: a storage warning whose branch could never run, and a silence watchdog
that switched itself off, both behind green tests.

So: change one operator, run the tests, see whether anything goes red. A
mutant that SURVIVES is a line no test constrains.

Two things this learned the hard way, both about not damaging the repo:

Mutations are spliced as TEXT, not by unparsing the AST. `ast.unparse` throws
comments away, so an AST round-trip rewrites the file even when the mutation
is reverted -- and on this codebase the comments are most of the value. The
AST is used only to find which lines hold a real operator, so a `<=` inside a
string or a comment is never touched.

The restore runs from a signal handler as well as a finally. The first version
had only the finally, a timeout killed it mid-run, and it left a live mutation
and a stripped file behind -- which then got read back as if it were the
original source.

    tools/mutate.py                  # the critical set, scoped
    tools/mutate.py clips:surplus    # one function
"""
import ast
import atexit
import re
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "tapo_h500"

# module:function -> the tests that exercise it. Each loses footage or strands
# the integration if it is wrong.
#
# Scoped rather than the whole suite per mutant: 1200 tests take three seconds,
# and a few dozen mutants of that is a tool nobody waits for. The trade is that
# a mutant killed only by some OTHER file reads as a survivor -- visible and
# worth a look, where a fifteen-minute run just gets ignored.
CRITICAL = {
    "clips:surplus": "test_api.py",                 # what retention deletes
    "clips:newest_matching": "test_retention.py",   # what retention protects
    "clips:expected_since": "test_silent.py",       # the watchdog's evidence
    "api:is_auth_failure": "test_config_flow.py",   # can strand a hub forever
    "repairs:_storage": "test_platforms.py",        # the pre-overwrite warning
    "clips:attach_detections": "test_api.py",       # how a clip is classified
}

# Written longest-first so `<=` is matched before `<`, and with a lookahead so
# `<` never matches the first half of `<=`.
SWAPS = [
    (re.compile(r"<="), "<"), (re.compile(r">="), ">"),
    (re.compile(r"<(?!=)"), "<="), (re.compile(r">(?!=)"), ">="),
    (re.compile(r"=="), "!="), (re.compile(r"!="), "=="),
    (re.compile(r"\band\b"), "or"), (re.compile(r"\bor\b"), "and"),
]

# Survivors proven to change nothing for any input. Checked by hand, once, and
# recorded here so the next run does not send somebody after them again.
#   surplus: `keep <= 0` and `len(items) <= keep` both fall through to
#            items[:-keep], which is [] at the boundary either way.
#   newest_matching: `keep <= 0` falls through to moments[:keep], and
#            moments[:0] is empty, which is what the guard returns.
KNOWN_EQUIVALENT = {
    ("clips:surplus", "<= -> <"),
    ("clips:newest_matching", "<= -> <"),
}

_RESTORE: dict[Path, str] = {}


def _restore_all(*_args):
    for path, text in list(_RESTORE.items()):
        path.write_text(text)
    _RESTORE.clear()


atexit.register(_restore_all)
for _signal in (signal.SIGTERM, signal.SIGINT):
    signal.signal(_signal, lambda *_: (_restore_all(), sys.exit(143)))


def operator_lines(source: str, function: str) -> set[int]:
    """Lines inside `function` that hold a real comparison or boolean.

    The AST is only asked WHERE. Anything it does not mark is left alone,
    which is what keeps a `>=` inside a docstring out of the mutant set.
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == function:
            lines = set()
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Compare, ast.BoolOp)):
                    lines.add(inner.lineno)
            return lines
    raise SystemExit(f"no function named {function}")


def mutants(source: str, lines: set[int]):
    """Every single-operator change, as (description, mutated source)."""
    rows = source.splitlines(keepends=True)
    for lineno in sorted(lines):
        original = rows[lineno - 1]
        # Comments hold operators too, and changing one proves nothing.
        code = original.split("#")[0]
        for pattern, replacement in SWAPS:
            for match in list(pattern.finditer(code)):
                mutated = (code[:match.start()] + replacement
                           + code[match.end():]
                           + original[len(code):])
                if mutated == original:
                    continue
                yield (f"line {lineno}: {match.group()} -> {replacement}",
                       "".join(rows[:lineno - 1]) + mutated
                       + "".join(rows[lineno:]))


def run_tests(pattern: str) -> bool:
    """True when the tests pass, meaning the mutant SURVIVED.

    Bounded: a mutant can hang rather than fail -- expected_since walks
    `while moment < now`, and widening that to `<=` never ends. A hang is the
    tests failing to constrain the code in the loudest way available, so it
    counts as killed rather than stalling the run.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover",
             "-s", str(ROOT / "tests"), "-p", pattern],
            capture_output=True, text=True, cwd=ROOT, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0


def mutate(target: str, pattern: str):
    module_name, _, function_name = target.partition(":")
    path = COMPONENT / f"{module_name}.py"
    original = path.read_text()
    _RESTORE[path] = original
    survivors, total = [], 0
    try:
        for description, mutated in mutants(
                original, operator_lines(original, function_name)):
            total += 1
            path.write_text(mutated)
            if run_tests(pattern):
                survivors.append(description)
    finally:
        path.write_text(original)
        _RESTORE.pop(path, None)
    return total, survivors


def main() -> int:
    targets = ({name: CRITICAL.get(name, "test_*.py") for name in sys.argv[1:]}
               if sys.argv[1:] else CRITICAL)
    gaps = 0
    for target, pattern in targets.items():
        total, survivors = mutate(target, pattern)
        if not total:
            print(f"--  {target:28s} no mutable operator found")
            continue
        real = [line for line in survivors
                if (target, line.split(": ", 1)[-1]) not in KNOWN_EQUIVALENT]
        mark = "ok " if not real else "GAP"
        print(f"{mark} {target:28s} "
              f"{total - len(survivors)}/{total} mutants killed"
              + (f"  ({len(survivors) - len(real)} known-equivalent)"
                 if len(survivors) != len(real) else ""))
        for line in real:
            print(f"       SURVIVED  {line}")
        gaps += len(real)
    print("\nA survivor is a line no test constrains. Check one before "
          "believing it: an\nEQUIVALENT mutant cannot change the output for "
          "any input, so it survives and\nmeans nothing. The ones already "
          "proven so are counted separately above.")
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
