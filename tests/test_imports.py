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
import re
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


class TheServicesLiveInTheirOwnModule(unittest.TestCase):
    """`__init__.py` is where an entry is set up and taken down.

    540 lines of request handling in the middle of that made both harder to
    find, and Home Assistant only ever looks for three names in the package
    body: setup, unload, and whether a device may be removed.
    """

    ROOT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
    INIT = (ROOT / "__init__.py").read_text()
    SERVICES = (ROOT / "services.py").read_text()

    def test_the_package_body_is_about_setting_up_and_tearing_down(self):
        self.assertLess(len(self.INIT.splitlines()), 260)
        for name in ("async_setup_entry", "async_unload_entry",
                     "async_remove_config_entry_device"):
            with self.subTest(name):
                self.assertIn(f"async def {name}", self.INIT)

    def test_no_handler_is_left_behind(self):
        for name in ("list_recordings", "download_recording", "name_face",
                     "daily_summary", "snooze", "backup_names"):
            with self.subTest(name):
                self.assertIn(f"async def {name}", self.SERVICES)
                self.assertNotIn(f"async def {name}", self.INIT)

    def test_all_thirteen_are_registered_and_all_thirteen_are_removed(self):
        registered = set(re.findall(r"\(SERVICE_(\w+), \w+, \w+_SCHEMA\)",
                                    self.SERVICES))
        listed = set(re.findall(r"\n    SERVICE_(\w+),",
                                self.SERVICES.split("SERVICES = (", 1)[1]
                                .split("\n)", 1)[0]))
        self.assertEqual(len(registered), 13)
        self.assertEqual(registered, listed,
                         "one registered and not listed never gets removed")

    def test_the_package_body_reaches_them_through_one_call(self):
        self.assertIn("services.async_register(hass)", self.INIT)
        self.assertIn("from .services import SERVICES", self.INIT)
