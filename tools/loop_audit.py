#!/usr/bin/env python3
"""Filesystem work reached from the event loop, in two passes.

Home Assistant runs everything on one loop. A stat call there is small, but
it is paid by every other integration in the installation, and Home Assistant
does not trap `Path.exists` -- so this class of mistake never warns. It only
ever surfaces as latency nobody can attribute, which is why it needs a tool
rather than a review.

Pass one: blocking calls written directly in an async body. Nested functions
are skipped -- a helper defined inside an async def and handed to
async_add_executor_job is correct, and counting it buries the real finding
under a dozen false ones.

Pass two: synchronous helpers that touch the filesystem, called from an async
body without an executor. This is the pass that matters; the finding this was
written for was three calls to a two-line helper.
"""
import ast
from pathlib import Path

BLOCKING = {"read_text", "write_text", "read_bytes", "write_bytes",
            "exists", "is_dir", "is_file", "iterdir", "glob",
            "stat", "mkdir", "unlink", "rmtree", "copy2",
            "rglob", "touch"}
COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "tapo_h500"


def shallow(node):
    """Children of a function, not entering nested definitions."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        item = stack.pop()
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            continue
        yield item
        stack.extend(ast.iter_child_nodes(item))


def main() -> int:
    findings = []
    touchers: dict[str, str] = {}

    # Which synchronous helpers touch the filesystem at all.
    for path in sorted(COMPONENT.glob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for inner in ast.walk(fn):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in BLOCKING):
                    touchers[fn.name] = path.name
                    break

    for path in sorted(COMPONENT.glob("*.py")):
        lines = path.read_text().splitlines()
        tree = ast.parse(path.read_text(), str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for item in shallow(fn):
                if not isinstance(item, ast.Call):
                    continue
                # Handed to the executor on the same line: that is the fix,
                # not the problem.
                if "async_add_executor_job" in lines[item.lineno - 1]:
                    continue
                name = (item.func.attr
                        if isinstance(item.func, ast.Attribute)
                        else getattr(item.func, "id", None))
                if name in BLOCKING or name in touchers:
                    where = touchers.get(name, path.name)
                    findings.append(
                        f"{path.name}:{item.lineno}  async {fn.name}: "
                        f"{name}()  [{where}]")

    if not findings:
        print("no blocking filesystem call runs directly on the event loop")
        return 0
    print("filesystem work reached from the event loop:")
    for line in sorted(findings):
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
