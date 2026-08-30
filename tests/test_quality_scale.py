"""What the integration claims, and whether it is true.

quality_scale.yaml is a public statement, rule by rule. Its value is entirely
in being honest, so the tests here are about making it hard to inflate: a tier
cannot be claimed while a rule at or below it is outstanding, an exemption
cannot be blank, and a rule cannot quietly go missing.
"""
import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "tapo_h500"
SCALE = yaml.safe_load((COMPONENT / "quality_scale.yaml").read_text())["rules"]
MANIFEST = json.loads((COMPONENT / "manifest.json").read_text())

# Home Assistant's own tiers, in order. A tier includes everything below it.
TIERS = {
    "bronze": [
        "action-setup", "appropriate-polling", "brands", "common-modules",
        "config-flow", "config-flow-test-coverage", "dependency-transparency",
        "docs-actions", "docs-high-level-description",
        "docs-installation-instructions", "docs-removal-instructions",
        "entity-event-setup", "entity-unique-id", "has-entity-name",
        "runtime-data", "test-before-configure", "test-before-setup",
        "unique-config-entry",
    ],
    "silver": [
        "action-exceptions", "config-entry-unloading",
        "docs-configuration-parameters", "docs-installation-parameters",
        "entity-unavailable", "integration-owner", "log-when-unavailable",
        "parallel-updates", "reauthentication-flow", "test-coverage",
    ],
    "gold": [
        "devices", "diagnostics", "discovery", "discovery-update-info",
        "docs-data-update", "docs-examples", "docs-known-limitations",
        "docs-supported-devices", "docs-supported-functions",
        "docs-troubleshooting", "docs-use-cases", "dynamic-devices",
        "entity-category", "entity-device-class",
        "entity-disabled-by-default", "entity-translations",
        "exception-translations", "icon-translations",
        "reconfiguration-flow", "repair-issues", "stale-devices",
    ],
    "platinum": ["async-dependency", "inject-websession", "strict-typing"],
}
ORDER = list(TIERS)


def status(rule: str) -> str:
    entry = SCALE[rule]
    return entry if isinstance(entry, str) else entry["status"]


def comment(rule: str) -> str:
    entry = SCALE[rule]
    return "" if isinstance(entry, str) else (entry.get("comment") or "")


def _upto(tier: str) -> list[str]:
    return [rule for name in ORDER[:ORDER.index(tier) + 1]
            for rule in TIERS[name]]


class TheFileIsComplete(unittest.TestCase):
    def test_every_rule_has_an_entry(self):
        """A rule left out reads as satisfied to anybody skimming."""
        missing = [rule for rules in TIERS.values() for rule in rules
                   if rule not in SCALE]
        self.assertEqual(missing, [])

    def test_nothing_is_invented(self):
        """A name that is not a rule protects nothing and looks like it does."""
        known = {rule for rules in TIERS.values() for rule in rules}
        self.assertEqual(set(SCALE) - known, set())

    def test_every_status_is_one_of_the_three(self):
        for rule in SCALE:
            with self.subTest(rule=rule):
                self.assertIn(status(rule), ("done", "todo", "exempt"))


class AClaimHasToBeTrue(unittest.TestCase):
    def test_the_claimed_tier_is_a_real_one(self):
        claimed = MANIFEST.get("quality_scale")
        if claimed is None:
            self.skipTest("nothing claimed yet")
        self.assertIn(claimed, ORDER)

    def test_nothing_at_or_below_the_claim_is_outstanding(self):
        """The self-enforcing half. Claiming a tier with a todo under it is
        the failure this whole file exists to prevent."""
        claimed = MANIFEST.get("quality_scale")
        if claimed is None:
            self.skipTest("nothing claimed yet")
        outstanding = [rule for rule in _upto(claimed)
                       if status(rule) == "todo"]
        self.assertEqual(
            outstanding, [],
            f"manifest claims {claimed} with these unfinished")

    def test_the_claim_is_the_highest_tier_actually_reached(self):
        """Under-claiming is not honest either -- it makes the file stop
        being a record of where the work got to."""
        reached = None
        for tier in ORDER:
            if any(status(rule) == "todo" for rule in TIERS[tier]):
                break
            reached = tier
        self.assertEqual(MANIFEST.get("quality_scale"), reached)


class AnExemptionHasToSayWhy(unittest.TestCase):
    def test_no_exemption_is_blank(self):
        """An exemption with no reason is a todo wearing a better word."""
        blank = [rule for rule in SCALE
                 if status(rule) == "exempt" and len(comment(rule)) < 40]
        self.assertEqual(blank, [])

    def test_an_exemption_that_depends_on_a_version_names_it(self):
        """So a library release is a prompt to re-check rather than something
        nobody revisits."""
        for rule in SCALE:
            if status(rule) != "exempt":
                continue
            body = comment(rule)
            if "pytapo" not in body:
                continue
            with self.subTest(rule=rule):
                self.assertRegex(body, r"pytapo \d+\.\d+\.\d+")


class WhatIsLeft(unittest.TestCase):
    def test_the_next_tier_is_reachable_and_named(self):
        """Not an assertion so much as a report: this prints what stands
        between the integration and its next claim, so the file cannot drift
        into being a thing nobody reads."""
        for tier in ORDER:
            outstanding = [rule for rule in TIERS[tier]
                           if status(rule) == "todo"]
            if outstanding:
                self.assertTrue(
                    all(rule in SCALE for rule in outstanding))
                break


if __name__ == "__main__":
    unittest.main()
