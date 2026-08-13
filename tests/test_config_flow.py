"""Config-flow shape, checked statically.

Home Assistant is not installed, so the flow cannot be executed here. What can
be checked without it is the part that actually breaks in front of a user: a
field offered by a form but missing from en.json renders as a raw key like
"poll_interval", and a setting written to the wrong place is stored, ignored,
and silently replaced by its default.
"""
import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "config_flow.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

package = types.ModuleType("tapo_h500")
package.__path__ = [str(COMPONENT)]
sys.modules.setdefault("tapo_h500", package)
const = importlib.import_module("tapo_h500.const")

# The few keys that come from homeassistant.const rather than the component.
HA_KEYS = {
    "CONF_HOST": "host",
    "CONF_USERNAME": "username",
    "CONF_PASSWORD": "password",
}


def _schema_fields(after: str) -> set[str]:
    """Constant names used in the first vol.Schema following a marker."""
    body = SOURCE.split(after, 1)[1].split("return self.async_show_form", 1)[0]
    # Digits matter: CONF_CONVERT_MP4 truncates to CONF_CONVERT_MP without them.
    names = re.findall(r"vol\.Required\(\s*(CONF_[A-Z0-9_]+)", body)
    keys = set()
    for name in names:
        keys.add(HA_KEYS.get(name) or getattr(const, name))
    return keys


class SetupForm(unittest.TestCase):
    def test_the_poll_interval_can_be_set_while_adding_the_hub(self):
        """It is the setting that decides whether notifications feel instant,
        so it should not be reachable only after the fact."""
        self.assertIn(const.CONF_POLL_INTERVAL, _schema_fields("async_step_user"))

    def test_the_interval_is_stored_where_the_coordinator_reads_it(self):
        """The coordinator reads entry.options. Left in data the value would be
        recorded, ignored, and replaced by the default."""
        self.assertRegex(
            SOURCE, r"options=\{CONF_POLL_INTERVAL: interval\}")
        # ...and removed from data, so one setting does not live in two places.
        self.assertRegex(SOURCE, r"user_input\.pop\(\s*CONF_POLL_INTERVAL")

    def test_both_forms_share_one_bound(self):
        """Two copies drifted apart once: the floor ended up above the default,
        so the default could not be saved. There must be exactly one."""
        self.assertEqual(len(re.findall(r"vol\.Range\(min=1, max=600\)", SOURCE)), 1)
        self.assertGreaterEqual(len(re.findall(r"\): POLL_INTERVAL,", SOURCE)), 2)

    def test_the_default_is_inside_the_bound(self):
        low, high = re.search(
            r"vol\.Range\(min=(\d+), max=(\d+)\)", SOURCE).groups()
        self.assertGreaterEqual(const.DEFAULT_POLL_INTERVAL, int(low))
        self.assertLessEqual(const.DEFAULT_POLL_INTERVAL, int(high))


class Labels(unittest.TestCase):
    def test_every_setup_field_has_a_label(self):
        """An unlabelled field renders as its raw key in the UI."""
        labelled = set(STRINGS["config"]["step"]["user"]["data"])
        self.assertEqual(_schema_fields("async_step_user") - labelled, set())

    def test_every_options_field_has_a_label(self):
        labelled = set(STRINGS["options"]["step"]["init"]["data"])
        self.assertEqual(_schema_fields("async_step_init") - labelled, set())


if __name__ == "__main__":
    unittest.main()
