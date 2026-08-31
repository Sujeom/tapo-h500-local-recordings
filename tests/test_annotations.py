"""The component says what its functions take and return.

No type checker runs here -- the integration must never take a runtime
dependency on one, and the gate has to work on a machine with nothing
installed. So this counts instead, as a ratchet.

The number is not the point. Dictionaries from the hub travel through a dozen
modules, and that is where a wrong assumption hides: this codebase has
already had a record nested two levels deeper than the code expected, where
every read silently returned the default and every test passed the same wrong
shape the code expected.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "tapo_h500"


def _tool():
    spec = importlib.util.spec_from_file_location(
        "annotations_tool", ROOT / "tools" / "annotations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TheCeilingIsHonest(unittest.TestCase):
    def setUp(self):
        self.tool = _tool()

    def test_it_matches_what_is_actually_there(self):
        """Not above and not below. A ceiling with slack in it lets the count
        drift back up without anybody noticing, and one below the truth means
        somebody lowered it without doing the work."""
        total = sum(len(self.tool.unannotated(path))
                    for path in sorted(COMPONENT.glob("*.py")))
        self.assertEqual(total, self.tool.CEILING)

    def test_it_only_ever_goes_down(self):
        """Asserted by the gate refusing both directions; this is the
        statement of intent."""
        self.assertEqual(self.tool.CEILING, 0,
                         "everything is annotated; the ceiling is the floor")


class WhatCountsAsAnnotated(unittest.TestCase):
    def setUp(self):
        self.tool = _tool()

    def _count(self, source: str) -> int:
        """Written to a temporary file rather than into tests/, so nothing
        is left behind and nothing here can be mistaken for a test file."""
        with tempfile.NamedTemporaryFile("w", suffix=".py",
                                         delete=False) as handle:
            handle.write(source)
            path = Path(handle.name)
        self.addCleanup(path.unlink, True)
        return len(self.tool.unannotated(path))

    def test_a_fully_annotated_function_does_not_count(self):
        self.assertEqual(self._count("def f(a: int) -> str: ...\n"), 0)

    def test_a_missing_return_counts(self):
        self.assertEqual(self._count("def f(a: int): ...\n"), 1)

    def test_a_missing_parameter_counts(self):
        self.assertEqual(self._count("def f(a) -> str: ...\n"), 1)

    def test_self_and_cls_are_not_expected_to_say(self):
        self.assertEqual(
            self._count("class C:\n    def f(self) -> None: ...\n"), 0)

    def test_keyword_only_and_positional_only_are_checked(self):
        """The easy ones to miss, because they are written past a marker."""
        self.assertEqual(self._count("def f(a: int, /, *, b) -> None: ...\n"), 1)
        self.assertEqual(self._count("def f(a, /, *, b: int) -> None: ...\n"), 1)

    def test_async_functions_count_too(self):
        self.assertEqual(self._count("async def f(a): ...\n"), 1)


class TheMarkerShips(unittest.TestCase):
    def test_py_typed_is_inside_the_integration(self):
        """PEP 561: at the repository root it means nothing, because HACS
        installs the component directory."""
        self.assertTrue((COMPONENT / "py.typed").is_file())


if __name__ == "__main__":
    unittest.main()
