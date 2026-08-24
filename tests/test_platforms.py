"""Diagnostics, logbook, repairs and the image platform.

The logbook phrasing is pure and is exercised for real, and so are the storage
checks, over a stubbed issue registry. The rest is checked statically, since it
imports the Home Assistant runtime. What is worth protecting is mostly about
what must NOT happen: diagnostics is a file people paste into public bug
reports, and a repair issue that never clears is worse than one that never
appears.
"""
import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
DIAG = (COMPONENT / "diagnostics.py").read_text()
REPAIRS = (COMPONENT / "repairs.py").read_text()
IMAGE = (COMPONENT / "image.py").read_text()
INIT = (COMPONENT / "__init__.py").read_text()
EVENT = (COMPONENT / "event.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
# Installs the Home Assistant stubs the component modules import.
import test_coordinator  # noqa: E402,F401

package = types.ModuleType("tapo_h500")
package.__path__ = [str(COMPONENT)]
sys.modules.setdefault("tapo_h500", package)
logbook = importlib.import_module("tapo_h500.logbook")
status = importlib.import_module("tapo_h500.status")

# repairs.py talks to the issue registry and to nothing else, so a recorder in
# its place is enough to run the checks for real. Registered here rather than
# in the shared harness, which every other test file inherits.
ISSUES = []
_registry = types.ModuleType("homeassistant.helpers.issue_registry")
_registry.async_create_issue = (
    lambda hass, domain, issue_id, **kwargs:
    ISSUES.append(("create", issue_id, kwargs)))
_registry.async_delete_issue = (
    lambda hass, domain, issue_id: ISSUES.append(("delete", issue_id, {})))
_registry.IssueSeverity = types.SimpleNamespace(WARNING="warning",
                                               ERROR="error")
sys.modules.setdefault("homeassistant.helpers.issue_registry", _registry)
repairs = importlib.import_module("tapo_h500.repairs")


class Diagnostics(unittest.TestCase):
    def test_no_credential_is_referenced_at_all(self):
        """This file gets pasted into public bug reports."""
        for secret in ("CONF_PASSWORD", "CONF_CLOUD_PASSWORD", "CONF_USERNAME",
                       "CONF_HOST", "entry.data"):
            self.assertNotIn(secret, DIAG, secret)

    def test_it_allow_lists_rather_than_blocking(self):
        """The hub's replies change between firmwares; a deny-list would leak
        whatever the next version adds."""
        self.assertIn("SAFE_READINGS", DIAG)
        self.assertIn("SAFE_CAMERA", DIAG)
        self.assertIn("for key in SAFE_READINGS", DIAG)

    def test_every_allow_listed_reading_actually_exists(self):
        """An allow-list of names nothing produces is a file full of nulls.

        Six of the sixteen were wrong -- `storage_total` for
        `storage_total_gb`, `led_enabled` for `led_on`, and so on -- so all
        three storage figures, the LED state, face detection and the audio
        slots came out null in every diagnostics download ever taken. Nothing
        failed; the file simply said nothing, which is the failure mode an
        allow-list is most prone to.
        """
        produced = set(status.hub_readings({}))
        listed = set(re.findall(
            r'"([a-z0-9_]+)"',
            DIAG.split("SAFE_READINGS = (", 1)[1].split(")", 1)[0]))
        self.assertEqual(listed - produced, set())

    def test_camera_aliases_are_not_included(self):
        """An alias is the owner's own words and can name a room or a person."""
        self.assertNotIn('"alias"', DIAG)
        self.assertIn('"index": index', DIAG)

    def test_face_names_are_counted_not_listed(self):
        self.assertIn('"named_faces": len(', DIAG)

    def test_timestamps_are_relative(self):
        """Absolute times would map a household's comings and goings."""
        self.assertIn("newest_recording_age", DIAG)


class Logbook(unittest.TestCase):
    def test_a_press_reads_as_a_sentence(self):
        self.assertEqual(logbook._phrase([6, 10, 17]),
                         "someone rang the doorbell (person)")

    def test_the_press_is_not_described_twice(self):
        """10 accompanies every 17 and would contradict it, exactly as in
        describe_detection."""
        phrase = logbook._phrase([2, 6, 10, 17])
        self.assertEqual(phrase.count("doorbell"), 1)
        self.assertNotIn("missed", phrase)

    def test_a_plain_detection_lists_what_fired(self):
        self.assertEqual(logbook._phrase([2, 6]), "motion, person")

    def test_an_unknown_code_shows_its_number(self):
        self.assertIn("type 31", logbook._phrase([31]))

    def test_nothing_at_all_still_reads(self):
        self.assertEqual(logbook._phrase([]), "activity")

    def test_the_integration_fires_the_event_it_describes(self):
        """A describer with no event to describe produces nothing."""
        self.assertIn('f"{DOMAIN}_event"', EVENT)
        self.assertIn('f"{DOMAIN}_event"', (COMPONENT / "logbook.py").read_text())


class Repairs(unittest.TestCase):
    def test_every_issue_clears_as_well_as_raises(self):
        """An issue that never clears is worse than one that never appears.

        Checked per function rather than by counting calls, which broke the
        moment a fourth issue was added and said nothing about which one had
        lost its clear.
        """
        for func in ("_storage", "_reachable", "_unnamed_faces"):
            body = REPAIRS.split(f"def {func}", 1)[1].split("\ndef ", 1)[0]
            self.assertIn("async_create_issue", body, func)
            self.assertIn("async_delete_issue", body, func)

    def test_it_warns_before_the_disk_is_full(self):
        """Loop recording does not fail at 100%, it discards the oldest footage
        silently, so warning at 100 would be warning after the loss."""
        percent = int(re.search(r"STORAGE_WARN_PERCENT = (\d+)", REPAIRS).group(1))
        self.assertLess(percent, 100)
        self.assertGreater(percent, 80)

    def test_both_issues_have_text(self):
        for key in ("storage_nearly_full", "hub_unreachable"):
            self.assertIn(key, STRINGS["issues"])
            self.assertIn("title", STRINGS["issues"][key])
            self.assertIn("description", STRINGS["issues"][key])

    def test_a_failed_poll_cannot_break_the_poll(self):
        coordinator = (COMPONENT / "coordinator.py").read_text()
        block = coordinator.split("from .repairs import async_check", 1)[1][:300]
        self.assertIn("except Exception", block)


class StorageWarning(unittest.TestCase):
    """The near-full warning, exercised by calling it rather than reading it.

    It asked `readings` for `storage_total` and `storage_free`, which nothing
    emits -- `status.hub_readings` publishes `storage_used_percent` -- so both
    were None, every poll took the unknown branch, and the warning could not
    be raised at any fullness. A test that matched the guard's source text
    stayed green through all of it, which is why these call the function.
    """

    def setUp(self):
        ISSUES.clear()

    @staticmethod
    def _poll(readings):
        """One check against a hub reporting `readings`.

        `hass` is passed straight through to the registry, so anything will
        do; `readings` is the whole input.
        """
        repairs._storage(object(), "e1",
                         types.SimpleNamespace(readings=readings))

    def test_a_nearly_full_hub_raises_the_warning(self):
        """The branch that could not be reached: at 96% the hub is one
        recording away from overwriting its oldest footage."""
        self._poll({"storage_used_percent": 96})
        self.assertEqual([action for action, _, _ in ISSUES], ["create"])
        _, issue_id, kwargs = ISSUES[0]
        self.assertEqual(issue_id, "storage_nearly_full_e1")
        self.assertEqual(kwargs["translation_key"], "storage_nearly_full")
        self.assertEqual(kwargs["translation_placeholders"], {"used": "96"})

    def test_the_threshold_itself_warns(self):
        """95 is where the warning is wanted, not where it starts being
        withheld: `<` and `<=` differ by exactly this poll."""
        self._poll({"storage_used_percent": repairs.STORAGE_WARN_PERCENT})
        self.assertEqual([action for action, _, _ in ISSUES], ["create"])

    def test_a_comfortable_hub_clears_the_warning(self):
        """An issue that never clears is worse than one that never appears."""
        self._poll({"storage_used_percent": 90})
        self.assertEqual(ISSUES, [("delete", "storage_nearly_full_e1", {})])

    def test_unknown_storage_is_not_treated_as_healthy(self):
        """A hub that reports no figure must not read as plenty of room --
        and must not raise the warning on a guess either. Missing and
        explicitly None arrive from different firmwares."""
        for readings in ({}, {"storage_used_percent": None}):
            with self.subTest(readings=readings):
                ISSUES.clear()
                self._poll(readings)
                self.assertEqual(
                    ISSUES, [("delete", "storage_nearly_full_e1", {})])


class NamePrompt(unittest.TestCase):
    """A face the hub keeps seeing is worth naming; one seen once is not."""

    def test_only_unnamed_faces_are_suggested(self):
        body = REPAIRS.split("def _unnamed_faces", 1)[1]
        self.assertIn("if face_id not in named", body)

    def test_a_threshold_separates_regulars_from_passers_by(self):
        const_src = (COMPONENT / "const.py").read_text()
        threshold = int(re.search(r"NAME_PROMPT_SIGHTINGS = (\d+)",
                                  const_src).group(1))
        # 1 or 2 would fire for anyone who ever walked past.
        self.assertGreaterEqual(threshold, 3)
        body = REPAIRS.split("def _unnamed_faces", 1)[1]
        self.assertIn(">= NAME_PROMPT_SIGHTINGS", body)

    def test_one_issue_covers_them_all(self):
        """A busy street would otherwise fill the repairs page with numbers."""
        # Bounded at the next def: an end-of-file slice counted every issue
        # any LATER function raises as if this one raised it.
        body = REPAIRS.split("def _unnamed_faces", 1)[1].split("\ndef ", 1)[0]
        self.assertEqual(body.count("async_create_issue"), 1)
        self.assertIn('"others"', body)

    def test_it_names_the_most_seen_one_first(self):
        body = REPAIRS.split("def _unnamed_faces", 1)[1]
        self.assertIn("reverse=True", body)

    def test_the_issue_clears_once_they_are_named(self):
        body = REPAIRS.split("def _unnamed_faces", 1)[1]
        self.assertIn("async_delete_issue", body)

    def test_the_prompt_says_where_to_do_it(self):
        text = STRINGS["issues"]["unnamed_face"]["description"]
        self.assertIn("Name faces", text)
        self.assertIn("{face_id}", text)


class Image(unittest.TestCase):
    def test_the_platform_is_registered(self):
        self.assertIn("Platform.IMAGE", INIT)

    def test_it_stamps_when_an_event_lands(self):
        """Without a changed timestamp the frontend never re-fetches."""
        self.assertIn("_attr_image_last_updated = dt_util.utcnow()", IMAGE)

    def test_it_is_driven_by_the_event_signal(self):
        self.assertIn('self.coordinator.signal("event", self.index)', IMAGE)

    def test_the_camera_entity_is_kept(self):
        """Removing it would break existing picture cards."""
        self.assertIn("Platform.CAMERA", INIT)
        self.assertTrue((COMPONENT / "camera.py").is_file())

    def test_it_has_a_label(self):
        self.assertIn("latest_event", STRINGS["entity"]["image"])


if __name__ == "__main__":
    unittest.main()


class NamePromptFix(unittest.TestCase):
    """The unnamed-face notice asks for the name itself.

    Repairs support fix flows with a form, so the notice that says "this
    face has been seen 12 times" can take the answer on the spot instead of
    pointing at the Configure page. Writing through async_update_entry means
    the existing options listener redraws every face surface -- the same
    path the name_face service and the card use.
    """

    def test_the_issue_is_fixable_and_carries_what_the_flow_needs(self):
        body = REPAIRS.split("def _unnamed_faces", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("is_fixable=True", body)
        self.assertIn('"face_id"', body.split("data=", 1)[1][:200])

    def test_the_flow_exists_and_writes_the_name(self):
        self.assertIn("async def async_create_fix_flow", REPAIRS)
        flow = REPAIRS.split("class NameFaceFlow", 1)[1]
        self.assertIn("CONF_FACE_NAMES", flow)
        self.assertIn("async_update_entry", flow)
        self.assertIn(".strip()", flow)

    def test_an_empty_answer_names_nobody(self):
        flow = REPAIRS.split("class NameFaceFlow", 1)[1]
        self.assertIn("and name:", flow)

    def test_the_form_has_words_on_it(self):
        strings = json.loads(
            (COMPONENT / "translations" / "en.json").read_text())
        step = strings["issues"]["unnamed_face"]["fix_flow"]["step"]["init"]
        self.assertTrue(step["title"])
        self.assertIn("{face_id}", step["description"])


class DevicePageTidiness(unittest.TestCase):
    """Analyst numbers live in the diagnostic section; glanceables lead.

    The hub device carries thirty-odd entities now. The page should open
    with what somebody actually checks -- activity, health, storage level
    -- and file the numbers that exist for statistics and graphs under
    Diagnostic, where they keep recording exactly as before. Nothing is
    removed or disabled; this is shelving, not pruning.
    """

    SENSOR = (COMPONENT / "sensor.py").read_text()

    def _block(self, key):
        import re
        match = re.search(
            r'key="%s".*?\n    \)' % key, self.SENSOR, re.S)
        self.assertIsNotNone(match, key)
        return match.group(0)

    def test_the_analyst_numbers_are_shelved(self):
        for key in ("storage_free", "recordings_1h", "busiest_hour",
                    "people_seen", "unknown_faces"):
            self.assertIn("EntityCategory.DIAGNOSTIC", self._block(key), key)

    def test_the_glanceables_lead(self):
        for key in ("storage_used", "hub_health", "last_activity",
                    "recordings_24h"):
            self.assertNotIn("EntityCategory", self._block(key), key)
