"""If this hub is ever discovered automatically, on evidence rather than a guess.

There is no dhcp matcher yet, and that is deliberate. A matcher needs this
hub's real OUI prefix and hostname; a wrong one makes Home Assistant offer
this integration to somebody who plugged in an unrelated TP-Link device, and
that is worse than typing an IP address once.

So this file guards the shape of the thing rather than testing it. It fails
the moment a matcher is added without the provenance of its values written
down, and it fails on the matchers that look right and match everything.
"""
import json
import re
import unittest
from pathlib import Path

import yaml

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
MANIFEST = json.loads((COMPONENT / "manifest.json").read_text())
FLOW = (COMPONENT / "config_flow.py").read_text()
SCALE = yaml.safe_load((COMPONENT / "quality_scale.yaml").read_text())["rules"]


def _rule(name):
    """The entry as written: a bare status, or a status with a reason."""
    entry = SCALE[name]
    if isinstance(entry, str):
        return entry, ""
    return entry["status"], entry.get("comment", "")

DISCOVERY = ("dhcp", "ssdp", "zeroconf", "bluetooth", "usb", "homekit", "mqtt")


class NoMatcherArrivesOnAGuess(unittest.TestCase):
    def _matchers(self):
        return [(kind, entry)
                for kind in DISCOVERY
                for entry in MANIFEST.get(kind, [])]

    def test_a_matcher_never_matches_everything(self):
        """"macaddress": "*" offers this integration for every device on the
        network. So does a hostname of "*"."""
        for kind, entry in self._matchers():
            for field, value in entry.items():
                with self.subTest(kind=kind, field=field):
                    self.assertNotIn(str(value).strip(), ("*", "", "**"))

    def test_a_mac_prefix_is_long_enough_to_mean_something(self):
        """An OUI is six hex digits. Fewer is a whole slice of the internet
        of things."""
        for kind, entry in self._matchers():
            prefix = entry.get("macaddress")
            if prefix is None:
                continue
            with self.subTest(prefix=prefix):
                self.assertGreaterEqual(
                    len(re.sub(r"[^0-9A-Fa-f]", "", prefix.rstrip("*"))), 6)

    def test_the_values_have_their_provenance_written_down(self):
        """Where each came from, in quality_scale.yaml beside the rule. A
        matcher nobody can trace is a matcher nobody can correct."""
        if not self._matchers():
            self.skipTest("no matcher yet, which is the current answer")
        self.assertRegex(_rule("discovery")[1].lower(),
                         r"observed|basicinfo|lease table")

    def test_a_matcher_comes_with_a_step_to_handle_it(self):
        """A manifest matcher with no async_step_dhcp makes Home Assistant
        offer a flow that raises."""
        for kind, _ in self._matchers():
            with self.subTest(kind=kind):
                self.assertIn(f"async_step_{kind}", FLOW)


class WhileThereIsNone(unittest.TestCase):
    def test_the_rule_says_why_rather_than_going_quiet(self):
        """An outstanding rule with no reason reads as an oversight; this one
        is a decision about evidence."""
        status, reason = _rule("discovery")
        self.assertEqual(status, "todo")
        self.assertIn("wrong one", reason,
                      "the reason has to say what a guess would cost")
        self.assertIn("test_discovery", reason,
                      "and point at what stops one being added")

    def test_setup_still_asks_for_the_address(self):
        """Which is the whole reason discovery would be worth having."""
        self.assertIn("CONF_HOST", FLOW)


if __name__ == "__main__":
    unittest.main()
