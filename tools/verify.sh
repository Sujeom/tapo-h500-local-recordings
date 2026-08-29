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

python -B -m unittest discover -s tests -p 'test_*.py' 2>&1 | tail -3
node tests/test_cards.mjs >/dev/null && echo "card tests OK"
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
