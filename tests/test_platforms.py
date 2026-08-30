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
const = importlib.import_module("tapo_h500.const")


class Diagnostics(unittest.TestCase):
    # The privacy promises are held in test_diagnostics_live: the file is
    # generated from a hub carrying credentials, MACs, a cloud account and
    # camera aliases, and none of them appear in it. Naming the constants a
    # leak would travel through cannot show that a new field did not carry
    # one. What stays here is the one thing about the source itself -- that
    # it is written as an allow-list at all.

    def test_it_allow_lists_rather_than_blocking(self):
        """The hub's replies change between firmwares; a deny-list would leak
        whatever the next version adds."""
        self.assertIn("SAFE_READINGS", DIAG)
        self.assertIn("SAFE_CAMERA", DIAG)
        self.assertIn("for key in SAFE_READINGS", DIAG)


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

    def test_the_prompt_takes_the_name_rather_than_pointing_at_a_page(self):
        """It is a fixable issue, so the notice asks for the name itself.

        Home Assistant's translation schema treats a description and a fix
        flow as alternatives -- a fixable issue carries a title and a flow --
        so what used to be said in the description is said on the form, which
        is where somebody is standing when they act on it.
        """
        issue = STRINGS["issues"]["unnamed_face"]
        self.assertNotIn("description", issue,
                         "a fixable issue may not carry one")
        text = issue["fix_flow"]["step"]["init"]["description"]
        self.assertIn("{face_id}", text)
        self.assertIn("{sightings}", text)
        self.assertIn("{cameras}", text)

    def test_the_flow_is_handed_what_its_wording_needs(self):
        """Those placeholders come from the flow's own dictionary, not the
        issue's, so they have to be passed through the issue's `data`."""
        body = REPAIRS.split("def _unnamed_faces", 1)[1]
        for key in ("sightings", "cameras", "others"):
            with self.subTest(key):
                self.assertIn(f'"{key}"', body)
        flow = REPAIRS.split("class NameFaceFlow", 1)[1]
        self.assertIn('("face_id", "sightings", "cameras", "others")', flow)


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


class _FakeCoordinator:
    """Only what the repair checks actually read.

    A real coordinator would drag a poll loop into a test about whether a
    notice is raised. Every attribute below is one a check reaches for, so a
    check growing a new dependency fails here loudly rather than silently
    reading a default that flatters it.
    """

    def __init__(self, **overrides):
        self.last_update_success = True
        self.readings = {}
        self.cameras = []
        self.media_status = "healthy"
        self.media_serving_empty = False
        self.download_failures = {}
        self.auto_restart_broken = False
        self.face_names = {}
        self.entry = types.SimpleNamespace(options={})
        self._tampered = []
        self._silent = []
        self._faces = {}
        self.__dict__.update(overrides)

    def tampered(self, within):
        return list(self._tampered)

    def silent_cameras(self, threshold):
        return list(self._silent)

    def faces_seen(self, *args, **kwargs):
        return dict(self._faces)


def run_check(check, **overrides):
    """Run one real repair check and return what it did to the registry."""
    ISSUES.clear()
    hass = types.SimpleNamespace(data={})
    getattr(repairs, check)(hass, "e1", _FakeCoordinator(**overrides))
    return ISSUES[0] if ISSUES else None


def _hass_holding(*coordinators):
    """A hass whose loaded entries carry these hubs.

    The clashing-folder check gathers every camera across every hub, which is
    the whole point of it -- two hubs is when sharing a folder stops being
    theoretical -- so the hubs have to be reachable the way they really are.
    """
    entries = [types.SimpleNamespace(entry_id=f"e{n}", runtime_data=hub)
               for n, hub in enumerate(coordinators, start=1)]
    return types.SimpleNamespace(
        config_entries=types.SimpleNamespace(
            async_loaded_entries=lambda domain=None: list(entries)))


class RepairChecksActuallyRun(unittest.TestCase):
    """Every check called for real, not matched as text.

    Eight of the nine were guarded only by asserting that the words
    "async_create_issue" and "async_delete_issue" appeared somewhere in the
    function body. That passes whether or not either branch can be reached --
    which is exactly how the storage warning stayed dead for months while its
    test went green. These call the function and read the registry.
    """

    def test_an_unreachable_hub_raises_and_a_reachable_one_clears(self):
        action, issue, kwargs = run_check("_reachable", last_update_success=False)
        self.assertEqual(action, "create")
        self.assertEqual(issue, "hub_unreachable_e1")
        self.assertEqual(kwargs["severity"], "error")
        self.assertEqual(run_check("_reachable", last_update_success=True)[0],
                         "delete")

    def test_a_wedged_media_service_raises(self):
        self.assertEqual(run_check("_media", media_status="wedged")[0], "create")

    def test_sessions_that_answer_with_nothing_also_raise(self):
        """The 2026-08-18 variant: the handshake is fine and the bytes are
        not, so the wedge check alone would call this healthy."""
        self.assertEqual(
            run_check("_media", media_serving_empty=True)[0], "create")

    def test_a_healthy_media_service_clears(self):
        self.assertEqual(run_check("_media")[0], "delete")

    def test_downloads_raise_only_after_the_third_failure(self):
        """One is a blip, two is a bad evening. Three in a row with no
        success between them is a pipeline that will fail the fourth time."""
        self.assertEqual(
            run_check("_downloads_failing",
                      download_failures={"Front": 2})[0], "delete")
        action, _, kwargs = run_check("_downloads_failing",
                                      download_failures={"Front": 3})
        self.assertEqual(action, "create")
        self.assertEqual(kwargs["translation_placeholders"]["cameras"], "Front")

    def test_a_paused_auto_restart_is_visible(self):
        self.assertEqual(
            run_check("_restart_ineffective", auto_restart_broken=True)[0],
            "create")
        self.assertEqual(run_check("_restart_ineffective")[0], "delete")

    def test_tampering_names_the_camera_and_counts_it(self):
        """Once is a knock; repeatedly is not."""
        action, _, kwargs = run_check(
            "_tampered", _tampered=[("Front", 1_786_600_000),
                                    ("Front", 1_786_599_000)])
        self.assertEqual(action, "create")
        self.assertEqual(kwargs["translation_placeholders"]["camera"], "Front")
        self.assertEqual(kwargs["translation_placeholders"]["count"], "2")
        self.assertEqual(run_check("_tampered")[0], "delete")

    def test_a_silent_camera_is_named(self):
        action, _, kwargs = run_check("_silent_cameras", _silent=["Front"])
        self.assertEqual(action, "create")
        self.assertEqual(kwargs["translation_placeholders"]["cameras"], "Front")
        self.assertEqual(run_check("_silent_cameras")[0], "delete")

    def test_an_unnamed_face_is_offered_for_naming(self):
        """Fixable, because the notice takes the name itself rather than
        pointing at a settings page."""
        faces = {"77": {"id": "77", "sightings": const.NAME_PROMPT_SIGHTINGS,
                        "cameras": ["Front"]}}
        action, _, kwargs = run_check("_unnamed_faces", _faces=faces)
        self.assertEqual(action, "create")
        self.assertTrue(kwargs["is_fixable"])
        self.assertEqual(kwargs["data"]["face_id"], "77")

    def test_a_face_already_named_is_not_offered(self):
        faces = {"77": {"id": "77", "sightings": const.NAME_PROMPT_SIGHTINGS}}
        self.assertEqual(
            run_check("_unnamed_faces", _faces=faces,
                      face_names={"77": "Alice"})[0], "delete")

    def test_a_rarely_seen_face_is_not_offered(self):
        faces = {"77": {"id": "77",
                        "sightings": const.NAME_PROMPT_SIGHTINGS - 1}}
        self.assertEqual(run_check("_unnamed_faces", _faces=faces)[0], "delete")

    def test_two_cameras_sharing_a_folder_are_named(self):
        """Compared as slugs: "Front Door" and "front door" look different in
        the app and are the same directory on disk, where one camera's
        recording answers "already downloaded" for the other.

        Case and spacing collapse; a hyphen does not. Writing this test is
        what caught the docstring next door claiming "front-door" collides
        with "Front Door", which camera_slug has never done.
        """
        mine = [{"alias": "Front Door"}, {"alias": "front door"}]
        coordinator = _FakeCoordinator(cameras=mine)
        ISSUES.clear()
        # The check gathers every camera across every hub, so the hub has to
        # be registered -- that is the whole point of it: two hubs is when
        # sharing a folder stops being theoretical.
        hass = _hass_holding(coordinator)
        repairs._clashing_names(hass, "e1", coordinator)
        action, issue, kwargs = ISSUES[0]
        self.assertEqual(action, "create")
        self.assertEqual(issue, "clashing_camera_names_e1")
        self.assertIn("front", kwargs["translation_placeholders"]["cameras"])

    def test_distinct_names_clear_the_notice(self):
        coordinator = _FakeCoordinator(
            cameras=[{"alias": "Front"}, {"alias": "Side"}])
        ISSUES.clear()
        hass = _hass_holding(coordinator)
        repairs._clashing_names(hass, "e1", coordinator)
        self.assertEqual(ISSUES[0][0], "delete")

    def test_every_check_is_dispatched_by_async_check(self):
        """A check nothing calls is a check that never runs."""
        dispatch = REPAIRS.split("def async_check", 1)[1].split("\ndef ", 1)[0]
        for name in ("_storage", "_reachable", "_unnamed_faces",
                     "_silent_cameras", "_clashing_names", "_media",
                     "_downloads_failing", "_restart_ineffective",
                     "_tampered"):
            self.assertIn(f"{name}(hass, entry_id, coordinator)", dispatch)


if __name__ == "__main__":
    unittest.main()
