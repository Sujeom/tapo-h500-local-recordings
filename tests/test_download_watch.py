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

        async def fake_download(hass, client, camera, start, end, convert):
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
