#!/usr/bin/env python3
"""Static checks for the bug classes this codebase has actually produced.

There is no ruff, flake8 or mypy here, and adding one means adding the first
third-party development dependency to a project that has deliberately avoided
them -- the test suite runs on stdlib unittest plus yaml, jinja2 and node.
The `ast` module covers the checks that would have caught something real:

- Unused imports. One was found by hand this week: `import re` outlived the
  regex it existed for, and nothing noticed.
- Mutable default arguments, which is a shared-state bug that presents as
  "the second call behaves differently" long after the edit that caused it.
- `except:` with no exception class, which swallows KeyboardInterrupt and
  SystemExit along with the error somebody meant to catch.
- `== None` / `!= None`, which is False for anything overriding __eq__.

Deliberately NOT a style checker. This codebase has a strong consistent voice
and a line-length rule would produce hundreds of findings that mean nothing,
which is how a lint gets switched off.

    tools/lint.py            # component + tools + tests
    tools/lint.py --fix      # only reports; there is nothing safe to autofix
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "custom_components" / "tapo_h500", ROOT / "tools",
           ROOT / "tests"]


def imported_names(tree: ast.AST):
    """(name, lineno, statement) for every import binding in the module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import a.b` binds `a`; `import a.b as c` binds `c`.
                bound = alias.asname or alias.name.split(".")[0]
                yield bound, node.lineno, alias.asname or alias.name
        elif isinstance(node, ast.ImportFrom):
            # `from __future__ import annotations` is a compiler directive, not
            # a binding anything references by name. Reporting it would be 32
            # findings that must all be ignored, which teaches people to ignore
            # the rest too.
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                yield (alias.asname or alias.name), node.lineno, alias.name


def used_names(tree: ast.AST) -> set[str]:
    """Every name the module mentions anywhere other than as an import.

    Attribute bases count (`re.sub` uses `re`), and so do strings inside
    __all__ and type annotations written as strings, because a name used only
    there is still used.
    """
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                used.add(base.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A forward-referenced annotation, or __all__. Cheap and it only
            # ever suppresses a finding, never invents one.
            used.update(part for part in node.value.replace(
                "[", " ").replace("]", " ").replace(",", " ").split())
    return used


def check(path: Path) -> list[str]:
    source = path.read_text()
    tree = ast.parse(source, str(path))
    found: list[str] = []
    rel = path.relative_to(ROOT)

    used = used_names(tree)
    for name, lineno, original in imported_names(tree):
        if name in used:
            continue
        line = source.splitlines()[lineno - 1]
        if "noqa" in line:                     # deliberate, and said so
            continue
        found.append(f"{rel}:{lineno}: unused import `{original}`")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    found.append(
                        f"{rel}:{node.lineno}: mutable default in "
                        f"`{node.name}` -- it is created once and shared")
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            found.append(
                f"{rel}:{node.lineno}: bare `except:` also catches "
                f"KeyboardInterrupt and SystemExit")
        elif isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) \
                        and isinstance(comparator, ast.Constant) \
                        and comparator.value is None:
                    found.append(
                        f"{rel}:{node.lineno}: use `is None`, not `== None`")
    return found


def main() -> int:
    findings: list[str] = []
    for target in TARGETS:
        for path in sorted(target.rglob("*.py")):
            findings.extend(check(path))
    for line in findings:
        print(line)
    print(f"\n{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
