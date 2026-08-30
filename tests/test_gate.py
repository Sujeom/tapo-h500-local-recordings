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


if __name__ == "__main__":
    unittest.main()
