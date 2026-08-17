"""Repeated download failures become a notice, not a private log line.

An automatic download that fails is a warning in a log nobody reads, and it
fails again on the next clip for the same reason -- ffmpeg missing, disk
full, the hub's media service refusing. Three in a row on one camera is a
pattern; the repairs page is where patterns belong.
"""
import asyncio
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
REPAIRS = (COMPONENT / "repairs.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
CAMERA = {"device_id": "cam0", "alias": "Front"}


def clip(start=NOW - 60):
    return {"startTime": start, "endTime": start + 15}


class Counting(unittest.TestCase):
    def setUp(self):
        self.coord, _ = harness._build()
        self.coord.cameras = [CAMERA, {"device_id": "cam1", "alias": "Side"}]
        self.outcomes: list = []

        async def fake_download(hass, client, camera, start, end, convert,
                                detected=None, faces=None):
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return {"path": "x.mp4", "bytes": 1}

        async def fake_verify(hass, path):
            return True

        self._patch("async_download_clip", fake_download)
        self._patch("async_verify", fake_verify)
        self._patch("async_prune", self._nothing)
        self._patch("existing_clip", lambda hass, camera, start: None)

    async def _nothing(self, *args):
        return []

    def _patch(self, name, value):
        self.addCleanup(setattr, coordinator_mod, name,
                        getattr(coordinator_mod, name))
        setattr(coordinator_mod, name, value)

    def _run(self, index=0):
        asyncio.run(self.coord._download(index, CAMERA, clip()))

    def _fail(self):
        return coordinator_mod.HomeAssistantError("no ffmpeg")

    def test_failures_are_counted(self):
        self.outcomes = [self._fail(), self._fail(), self._fail()]
        for _ in range(3):
            self._run()
        self.assertEqual(self.coord.download_failures, {"Front": 3})

    def test_a_success_resets_the_count(self):
        """Consecutive is the whole point: two failures around a success is
        a flaky afternoon, not a broken pipeline."""
        self.outcomes = [self._fail(), self._fail(), {}]
        for _ in range(3):
            self._run()
        self.assertEqual(self.coord.download_failures, {})

    def test_cameras_are_counted_separately(self):
        self.outcomes = [self._fail(), self._fail()]
        self._run(index=0)
        self._run(index=1)
        self.assertEqual(set(self.coord.download_failures.values()), {1})

    def test_a_clip_that_downloads_but_does_not_decode_counts(self):
        """The other failed outcome: bytes arrived, ffprobe says garbage.
        The clip is removed to be fetched again, and that is still a
        pipeline failing."""
        async def bad_verify(hass, path):
            return False

        self._patch("async_verify", bad_verify)
        self._patch("existing_clip",
                    lambda hass, camera, start: Path("/media/x.mp4"))

        async def fake_unlink(fn, *args):
            return None

        self.coord.hass.async_add_executor_job = fake_unlink
        self.outcomes = [{}]
        # existing_clip returning a path would early-return before the
        # download; only the post-download check should see it.
        real = coordinator_mod.existing_clip
        calls = {"n": 0}

        def once(hass, camera, start):
            calls["n"] += 1
            return None if calls["n"] == 1 else Path("/media/x.mp4")

        self._patch("existing_clip", once)
        self._run()
        self.assertEqual(self.coord.download_failures, {"Front": 1})


class Issue(unittest.TestCase):
    def test_it_is_checked_with_the_others(self):
        self.assertIn("_downloads_failing(hass, entry_id, coordinator)",
                      REPAIRS)

    def test_it_clears_itself(self):
        body = REPAIRS.split("def _downloads_failing", 1)[1].split(
            "\ndef ", 1)[0]
        self.assertIn("async_delete_issue", body)

    def test_three_in_a_row_is_the_line(self):
        self.assertIn("DOWNLOAD_FAIL_ALERT = 3", REPAIRS)

    def test_it_names_the_camera(self):
        body = REPAIRS.split("def _downloads_failing", 1)[1].split(
            "\ndef ", 1)[0]
        self.assertIn('"cameras"', body)

    def test_it_has_a_title_and_a_description(self):
        issue = STRINGS["issues"]["downloads_failing"]
        self.assertTrue(issue["title"])
        self.assertIn("{cameras}", issue["title"] + issue["description"])


if __name__ == "__main__":
    unittest.main()


clips_mod = importlib.import_module("tapo_h500.clips")
INIT = (COMPONENT / "__init__.py").read_text()
SERVICES = (COMPONENT / "services.yaml").read_text()


class EndForStart(unittest.TestCase):
    """The indexed end for a clip named only by its start.

    The notification's Save button knows the event's start time and nothing
    else -- the detection log carries no end -- so the service has to find
    the end in the clip index itself. Same one-second tolerance the
    detection-to-clip matching uses: the two indexes need not agree to the
    second.
    """

    CLIPS = [{"startTime": 1000, "endTime": 1015},
             {"startTime": 1200, "endTime": 1230}]

    def test_the_matching_clips_end_comes_back(self):
        self.assertEqual(clips_mod.end_for_start(self.CLIPS, 1200), 1230)

    def test_one_second_out_still_matches(self):
        self.assertEqual(clips_mod.end_for_start(self.CLIPS, 1001), 1015)

    def test_no_match_is_none_not_a_neighbour(self):
        """Two seconds out is a different recording; downloading the
        neighbour would save the wrong moment."""
        self.assertIsNone(clips_mod.end_for_start(self.CLIPS, 1003))
        self.assertIsNone(clips_mod.end_for_start([], 1000))

    def test_a_clip_without_an_end_cannot_answer(self):
        self.assertIsNone(clips_mod.end_for_start(
            [{"startTime": 1000}], 1000))


class SaveFromTheService(unittest.TestCase):
    def test_end_time_is_optional_now(self):
        self.assertIn('vol.Optional("end_time")', INIT)
        body = SERVICES.split("download_recording:", 1)[1].split(
            "\ndelete_recording:", 1)[0]
        end = body.split("end_time:", 1)[1].split("convert_to_mp4:", 1)[0]
        self.assertNotIn("required: true", end)

    def test_a_missing_end_is_looked_up_in_the_index(self):
        body = INIT.split("async def download_recording", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("end_for_start", body)
        self.assertIn("recent", body)

    def test_an_unindexed_clip_is_a_clear_refusal(self):
        body = INIT.split("async def download_recording", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("ServiceValidationError", body)


CONFIG_FLOW = (COMPONENT / "config_flow.py").read_text()
CARD_JS = (COMPONENT / "www" / "tapo-h500-card.js").read_text()


class DefaultWindow(unittest.TestCase):
    """The server-side twin of the card's windowDates.

    A local day is not a UTC day: west of UTC, today starts on yesterday's
    UTC date, so a default of "today, UTC" silently hides every evening. The
    harness pins local time to -07:00 exactly so this class of bug fails.
    """

    NOW = 1_786_600_000  # 2026-08-13 05:46 UTC = 2026-08-12 22:46 local

    def test_one_local_day_spans_two_utc_dates_in_the_evening(self):
        start, end = clips_mod.window_dates(1, self.NOW)
        self.assertEqual((start, end), ("20260812", "20260813"))

    def test_more_days_widen_backwards_never_forwards(self):
        one = clips_mod.window_dates(1, self.NOW)
        three = clips_mod.window_dates(3, self.NOW)
        self.assertEqual(one[1], three[1])
        self.assertLess(three[0], one[0])
        self.assertEqual(clips_mod.window_dates(3, self.NOW)[0], "20260810")

    def test_zero_and_negative_mean_one(self):
        self.assertEqual(clips_mod.window_dates(0, self.NOW),
                         clips_mod.window_dates(1, self.NOW))
        self.assertEqual(clips_mod.window_dates(-5, self.NOW),
                         clips_mod.window_dates(1, self.NOW))


class GlobalDays(unittest.TestCase):
    """One setting instead of eight card editors.

    A card whose owner never set days: sends no dates, and the service fills
    the window from the Configure option. A card with its own days -- set by
    the owner, or a summary-family card whose class default is a week --
    keeps sending exact dates and is untouched.
    """

    def test_the_service_fills_missing_dates_from_the_option(self):
        body = INIT.split("async def list_recordings", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("CONF_CARD_DAYS", body)
        self.assertIn("window_dates", body)

    def test_the_option_is_on_the_settings_form(self):
        settings = CONFIG_FLOW.split("async_step_settings", 1)[1]
        self.assertIn("CONF_CARD_DAYS", settings)

    def test_the_option_is_not_a_reload(self):
        """Read at call time; a reload would buy a login for a number."""
        import importlib as _importlib
        const_mod = _importlib.import_module("tapo_h500.const")
        self.assertNotIn(const_mod.CONF_CARD_DAYS, const_mod.RELOAD_ON_CHANGE)

    def test_the_card_only_sends_dates_it_was_given(self):
        self.assertIn("windowFor(", CARD_JS)
        self.assertIn("this._explicitDays", CARD_JS)

    def test_the_label_says_what_was_actually_shown(self):
        """A card following the global option must not caption itself with
        its own default."""
        self.assertIn("response.days", CARD_JS)
