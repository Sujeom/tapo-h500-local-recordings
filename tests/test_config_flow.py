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
        # init is now a menu; the settings form moved to its own step.
        labelled = set(STRINGS["options"]["step"]["settings"]["data"])
        self.assertEqual(_schema_fields("async_step_settings") - labelled, set())


class FaceNaming(unittest.TestCase):
    """Names are set from the integration's own screen, not from a card."""

    def test_the_options_menu_offers_naming(self):
        menu = STRINGS["options"]["step"]["init"]["menu_options"]
        self.assertIn("faces", menu)
        self.assertIn("settings", menu)

    def test_saving_settings_cannot_wipe_the_names(self):
        """Options are replaced wholesale on save. The settings form does not
        ask about face names, so without merging, saving any option at all
        silently deleted every name."""
        self.assertIn("def _merged", SOURCE)
        self.assertRegex(SOURCE, r"return \{\*\*self\.config_entry\.options, \*\*user_input\}")
        self.assertIn("self.async_create_entry(data=self._merged(user_input))", SOURCE)

    def test_saving_names_cannot_wipe_the_settings(self):
        """The same hazard in the other direction."""
        self.assertIn("data={**self.config_entry.options, CONF_FACE_NAMES: names}",
                      SOURCE)

    def test_already_named_faces_stay_editable(self):
        """Otherwise a name could be added but never corrected once that
        person stopped appearing in the window."""
        self.assertIn("set(seen) | set(names)", SOURCE)

    def test_clearing_a_box_removes_the_name(self):
        self.assertIn("names.pop(str(face_id), None)", SOURCE)

    def test_no_faces_yet_is_explained(self):
        self.assertIn('self.async_abort(reason="no_faces")', SOURCE)
        self.assertIn("no_faces", STRINGS["options"]["abort"])

    def test_a_photo_is_linked_so_the_number_can_be_matched_to_a_person(self):
        """The whole point: nobody can name 123456789012 from memory."""
        self.assertIn("def _photo_url", SOURCE)
        self.assertIn("see photo", SOURCE)
        self.assertIn("signed_url(self.hass, path)", SOURCE)

    def test_the_link_is_absolute_so_it_actually_resolves(self):
        """signed_url returns a root-relative path. A card puts that in an
        <img src> and the browser resolves it, but a markdown link is handled
        by the frontend router, which treats /media/local/... as an in-app
        route, finds no such page and goes nowhere. That is why the first
        version of this link did nothing when clicked.
        """
        body = SOURCE.split("def _photo_url", 1)[1]
        self.assertIn("get_url(self.hass)", body)
        self.assertIn('rstrip(\'/\')', body)

    def test_an_installation_with_no_configured_url_still_gets_something(self):
        """The relative form is still correct for anything resolving it
        against the origin, so offer it rather than nothing."""
        body = SOURCE.split("def _photo_url", 1)[1]
        self.assertIn("except NoURLAvailableError:", body)
        self.assertIn("return signed", body)

    def test_no_link_is_offered_before_the_clip_has_downloaded(self):
        """The thumbnail is written by the download, so linking
        unconditionally would offer a dead link for anyone seen this minute."""
        body = SOURCE.split("def _photo_url", 1)[1].split("async def", 1)[0]
        self.assertIn("if not path.is_file():", body)
        self.assertIn("return None", body)

    def test_the_disk_check_does_not_block_the_event_loop(self):
        """is_file() on every face is filesystem work inside a callback."""
        self.assertIn("async_add_executor_job(\n            self._face_lines",
                      SOURCE)

    def test_the_form_says_what_each_number_is(self):
        """A column of raw ids with text boxes tells nobody anything."""
        self.assertIn("description_placeholders", SOURCE)
        self.assertIn("{faces}", STRINGS["options"]["step"]["faces"]["description"])


if __name__ == "__main__":
    unittest.main()
