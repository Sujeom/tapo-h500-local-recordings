"""The commit gate has to fail when something fails.

It did not. `node tests/test_cards.mjs >/dev/null && echo "card tests OK"`
looks like a check and is not one: under `set -e` bash suppresses errexit for
the left side of an AND list, so a failing command there is stepped over and
the script carries on to exit zero. The card suite went red, the gate said
nothing, and the commit it was guarding went in.

These run the shell rather than reasoning about it, because that is the only
way to be sure which of the two behaviours this bash actually has.
"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
VERIFY = (ROOT / "tools" / "verify.sh").read_text()


def sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True)


class TheTrapIsReal(unittest.TestCase):
    """Shown, not asserted from memory. If a future bash changes this, the
    rule below stops being necessary and this test says so."""

    def test_a_failure_on_the_left_of_an_and_does_not_stop_the_script(self):
        result = sh('set -euo pipefail\n'
                    'false >/dev/null && echo "not reached"\n'
                    'echo "carried on"')
        self.assertEqual(result.returncode, 0)
        self.assertIn("carried on", result.stdout)

    def test_a_bare_failure_does_stop_it(self):
        result = sh('set -euo pipefail\nfalse\necho "carried on"')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("carried on", result.stdout)

    def test_an_if_reports_it(self):
        """The shape the gate uses instead."""
        result = sh('set -euo pipefail\n'
                    'if ! false; then echo "caught"; exit 1; fi\n'
                    'echo "not reached"')
        self.assertEqual(result.returncode, 1)
        self.assertIn("caught", result.stdout)
        self.assertNotIn("not reached", result.stdout)


class EveryCheckIsWiredToFail(unittest.TestCase):
    def test_nothing_is_gated_behind_an_and(self):
        """One line per check, and none of them announcing success from the
        right-hand side of an `&&`."""
        offenders = [line.strip() for line in VERIFY.splitlines()
                     if "&&" in line and not line.strip().startswith("#")]
        self.assertEqual(offenders, [])

    def test_it_still_stops_at_the_first_failure(self):
        self.assertIn("set -euo pipefail", VERIFY)

    def test_the_card_suite_is_checked_rather_than_announced(self):
        self.assertIn("if ! card_output=$(node tests/test_cards.mjs 2>&1); then",
                      VERIFY)
        self.assertIn("exit 1", VERIFY)


class AFailureSaysWhichOne(unittest.TestCase):
    """A gate that fails without saying why costs an afternoon.

    The suite was piped straight to `tail -3`, so a failure printed
    "FAILED (errors=1)" and threw away the name of the test and its
    traceback. In a CI log that is unrecoverable: the run is over, and the
    only way to find out what broke is to reproduce the runner locally --
    which is what it took.
    """

    def test_the_suite_is_captured_rather_than_piped_away(self):
        self.assertIn("if ! suite=$(python -B -m unittest discover", VERIFY)

    def test_a_failure_prints_more_than_the_summary(self):
        failing = VERIFY.split("if ! suite=", 1)[1].split("fi", 1)[0]
        self.assertIn("tail -60", failing)
        self.assertIn("exit 1", failing)

    def test_a_pass_still_prints_only_the_summary(self):
        """Sixty lines of dots on every green run is a log nobody reads."""
        self.assertIn('printf \'%s\\n\' "$suite" | tail -3', VERIFY)


class TheCheckoutHasWhatTheTestsRead(unittest.TestCase):
    """Two tests read git history, and a shallow checkout has none.

    `actions/checkout` fetches one commit and no tags unless asked. The
    release tool's tag parsing then had nothing to parse, and the check that
    the manifest version is at or ahead of the last release skipped itself.
    One failed in CI and passed everywhere else; the other quietly checked
    nothing.
    """

    WORKFLOW = (ROOT / ".github" / "workflows" / "verify.yml").read_text()

    def test_the_verify_job_asks_for_the_whole_history(self):
        self.assertIn("fetch-depth: 0", self.WORKFLOW)

    def test_the_tests_that_need_it_skip_rather_than_error_without_it(self):
        """A source tarball has no `.git` at all, and that is not a bug in
        the release tool."""
        source = (ROOT / "tests" / "test_release_tool.py").read_text()
        self.assertIn("skipTest", source)
        self.assertIn("CalledProcessError", source)


class TheCoverageFloorIsARatchet(unittest.TestCase):
    """Nine modules shipped at 0.0% coverage and nothing said so.

    The floors are ratchets, not targets: the total sits just under where the
    suite actually is, so improvement is kept rather than demanded, and the
    per-module floor exists so a NEW untested module fails the build instead
    of waiting for somebody to go looking.
    """

    COVERAGE = (ROOT / "tools" / "coverage.py").read_text()

    def test_ci_runs_the_gate_not_the_report(self):
        workflow = (ROOT / ".github" / "workflows" / "verify.yml").read_text()
        self.assertIn("tools/coverage.py --gate", workflow)

    def test_the_floors_exist_and_are_sane(self):
        import re
        total = float(re.search(r"FLOOR_TOTAL = ([\d.]+)", self.COVERAGE)[1])
        module = float(re.search(r"FLOOR_MODULE = ([\d.]+)", self.COVERAGE)[1])
        self.assertGreaterEqual(total, 70.0)
        self.assertGreaterEqual(module, 10.0)
        self.assertLess(module, total,
                        "the module floor is the tripwire, not the bar")

    def test_a_module_under_the_floor_fails_the_gate(self):
        """Driven, not read: the gate function itself, fed a failing row.

        Its verdicts print, and this suite is itself run in-process by
        coverage.py -- so without the redirect these fixtures' GATE lines
        land in the real run's output, reading like the real verdict.
        """
        import contextlib
        import importlib.util
        import io
        spec = importlib.util.spec_from_file_location(
            "coverage_tool", ROOT / "tools" / "coverage.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        bad = [("new_module.py", 0, 50, [])]
        good = [("new_module.py", 45, 50, [])]
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            self.assertEqual(tool._gate(bad, 90.0), 2)
            self.assertEqual(tool._gate(good, tool.FLOOR_TOTAL + 1), 0)
            self.assertEqual(tool._gate(good, tool.FLOOR_TOTAL - 1), 2)
        self.assertIn("new_module.py", said.getvalue(),
                      "a breach names the module it is about")


if __name__ == "__main__":
    unittest.main()
