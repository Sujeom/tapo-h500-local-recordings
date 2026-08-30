#!/usr/bin/env bash
# Everything that has to pass before a commit. Exits non-zero on the first
# failure, so it can gate a commit with && rather than being read by eye.
#
# -B on purpose. Cached bytecode has masked a real failure here twice: the .pyc
# header records the source mtime in whole seconds and the file size, so an
# edit of the same length made within a second of the last run is invisible to
# the invalidation check.
set -euo pipefail
cd "$(dirname "$0")/.."

# The summary when it passes, the failures when it does not. Piping straight
# to `tail -3` printed "FAILED (errors=1)" and threw away the name of the test
# and its traceback -- which is exactly what you need, and is unrecoverable
# from a CI log that has already scrolled past.
if ! suite=$(python -B -m unittest discover -s tests -p 'test_*.py' 2>&1); then
    printf '%s\n' "$suite" | grep -vE '^(\.|ok |test_)' | tail -60
    echo "tests FAILED"
    exit 1
fi
printf '%s\n' "$suite" | tail -3
# NOT `node ... && echo OK`. Under `set -e` bash suppresses errexit for the
# left side of an AND list, so a failing command there is stepped over and the
# script carries on to exit 0. This gate silently passed a broken card suite
# that way, and the commit it was guarding went in.
if ! card_output=$(node tests/test_cards.mjs 2>&1); then
    printf '%s\n' "$card_output" | grep -E "^ *FAIL" || true
    echo "card tests FAILED"
    exit 1
fi
echo "card tests OK"
# Same `if !` shape, same reason: a bare command here would be stepped over.
if ! card_coverage=$(python -B tools/card_coverage.py --gate 2>&1); then
    printf '%s\n' "$card_coverage"
    exit 1
fi
printf '%s\n' "$card_coverage" | tail -1
# Filesystem work on the event loop. Home Assistant does not trap Path.exists,
# so this class of mistake never warns -- it only ever shows up as latency
# nobody can attribute. Three calls to one two-line helper were live when this
# was written.
if ! loop_audit=$(python -B tools/loop_audit.py 2>&1); then
    printf '%s\n' "$loop_audit"
    exit 1
fi
printf '%s\n' "$loop_audit"
python -B tools/lint.py

python -B - <<'PY'
import ast
import json
import pathlib
import sys

import yaml


class Loader(yaml.SafeLoader):
    """!input is Home Assistant's own tag; keep it as data so blueprints parse."""


Loader.add_constructor("!input", lambda loader, node: None)

root = pathlib.Path(".")
modules = sorted((root / "custom_components" / "tapo_h500").glob("*.py"))
for path in modules:
    ast.parse(path.read_text(), str(path))
print(f"{len(modules)} modules parse")

# Every test file ends with unittest.main(). When content gets appended after
# it, running that file directly collects only what was defined above it and
# reports OK -- thirty files had drifted that way at once, one of them running
# 23 of its 70 tests. Discovery hides it completely, so nothing but this
# notices.
stranded = []
for path in sorted((root / "tests").glob("test_*.py")):
    tree = ast.parse(path.read_text(), str(path))
    guard = next((node.lineno for node in tree.body
                  if isinstance(node, ast.If)
                  and ast.dump(node.test).find("__main__") != -1), None)
    if guard is None:
        continue
    below = [node.name for node in tree.body
             if getattr(node, "lineno", 0) > guard
             and isinstance(node, (ast.ClassDef, ast.FunctionDef,
                                   ast.AsyncFunctionDef))]
    if below:
        stranded.append(f"{path.name}: {', '.join(below[:3])}")
if stranded:
    print("definitions below the unittest.main() guard, so running the file "
          "directly skips them:")
    for line in stranded:
        print(f"  {line}")
    sys.exit(1)

documents = [path for path in sorted(root.rglob("*.yaml"))
             if ".venv" not in path.parts and ".git" not in path.parts]
for path in documents:
    yaml.load(path.read_text(), Loader=Loader)
print(f"{len(documents)} yaml files valid")

for path in sorted(root.glob("custom_components/tapo_h500/**/*.json")):
    json.loads(path.read_text())
print("json valid")

# Every entity translation key an entity declares must exist, or the frontend
# shows the raw key as the name.
strings = json.loads(
    (root / "custom_components/tapo_h500/translations/en.json").read_text())
known = {platform: set(keys)
         for platform, keys in strings.get("entity", {}).items()}
missing = []
for path in modules:
    platform = path.stem
    if platform not in known:
        continue
    source = path.read_text()
    for key in set(__import__("re").findall(
            r'_attr_translation_key = "([\w]+)"', source)):
        if key not in known[platform]:
            missing.append(f"{platform}.{key}")
if missing:
    sys.exit(f"translation keys missing from en.json: {missing}")
print("translation keys resolve")
PY
