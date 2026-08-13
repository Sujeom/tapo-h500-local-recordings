"""Every name a module uses is one it can actually reach.

This exists because of a real bug: an edit removed `from .sensor import
hub_device` from binary_sensor.py while two classes still called it. Nothing
caught it. The whole suite is static or stubbed, so a NameError that only
happens when Home Assistant constructs the entity is invisible — and the
entity would simply fail to load, which looks like the integration quietly
having fewer sensors than it should.

Cheap to run and covers every module, including ones with no tests of their
own.
"""
import ast
import builtins
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
# Module dunders exist without being bound anywhere in the source.
BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
}


def _bound(tree: ast.AST) -> set[str]:
    """Every name the module binds, by any means."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            # `except X as err` binds err for the block.
            names.add(node.name)
        elif isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            names.update(node.names)
    return names


class Resolvable(unittest.TestCase):
    def test_no_module_uses_a_name_it_never_defines(self):
        problems = {}
        for path in sorted(COMPONENT.glob("*.py")):
            tree = ast.parse(path.read_text())
            used = {node.id for node in ast.walk(tree)
                    if isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)}
            missing = sorted(used - _bound(tree) - BUILTINS)
            if missing:
                problems[path.name] = missing
        self.assertEqual(problems, {})

    def test_the_check_covers_every_module(self):
        """A guard that silently scanned nothing would be worse than none."""
        self.assertGreater(len(list(COMPONENT.glob("*.py"))), 20)


if __name__ == "__main__":
    unittest.main()
