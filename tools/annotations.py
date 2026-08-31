#!/usr/bin/env python3
"""How much of the component is annotated, as a ratchet.

No type checker is installed here and the integration must never take a
runtime dependency on one, so this counts instead: a function is annotated
when every parameter and the return both say what they are. It is a floor
that only moves down, the same shape as the coverage gate and for the same
reason -- a target nobody can measure is a target nobody reaches.

The value is not the number. Dictionaries from the hub travel through a dozen
modules, and that is where a wrong assumption hides: a record nested two
levels deeper than the code expected, every read silently returning the
default, and a test passing the same wrong shape as the code expects.

    tools/annotations.py          # the number, worst module first
    tools/annotations.py --gate   # exit nonzero if it has gone up
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "tapo_h500"

# Lowered as modules are annotated, never raised.
CEILING = 51


def unannotated(path: Path) -> list[tuple[int, str]]:
    """(line, name) for every function that does not fully say what it is."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = [arg for arg in node.args.args
                if arg.arg not in ("self", "cls")]
        args += node.args.posonlyargs + node.args.kwonlyargs
        if any(arg.annotation is None for arg in args) or node.returns is None:
            found.append((node.lineno, node.name))
    return found


def main() -> int:
    rows = [(path.name, unannotated(path))
            for path in sorted(COMPONENT.glob("*.py"))]
    total = sum(len(found) for _, found in rows)
    show = "--missing" in sys.argv
    for name, found in sorted(rows, key=lambda row: -len(row[1])):
        if not found:
            continue
        print(f"{name:22s} {len(found):3d}")
        if show:
            for lineno, fn in found:
                print(f"    {lineno:5d}  {fn}")
    print(f"{'TOTAL':22s} {total:3d} unannotated, ceiling {CEILING}")
    if "--gate" in sys.argv:
        if total > CEILING:
            print(f"GATE: {total} unannotated is above the ceiling {CEILING}")
            return 2
        if total < CEILING:
            print(f"GATE: {total} is below the ceiling {CEILING}; lower "
                  f"CEILING to {total} so it cannot drift back")
            return 2
        print(f"annotation gate OK ({total} unannotated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
