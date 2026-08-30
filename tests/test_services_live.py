"""The thirteen services, called rather than read.

services.py was the largest low-coverage module left: 366 lines at 24%, and it
is the card's and every automation's whole API. These register the real
handlers against a recording hass and call them, with only the pieces that
reach a hub or a disk replaced.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import ha_stubs  # noqa: E402

from homeassistant.exceptions import (  # noqa: E402
    HomeAssistantError, ServiceValidationError,
)

services = ha_stubs.real_module("services")
dt_util = sys.modules["homeassistant.util.dt"]
NOW = int(dt_util.utcnow().timestamp())


def clip(start, length=15, faces=()):
    made = {"startTime": start, "endTime": start + length,
            "events_1": 1 << 5}
    if faces:
        made["event_info"] = [{"face_id": face} for face in faces]
    return made


class _Call:
    def __init__(self, **data):
        self.data = data


class _World(unittest.TestCase):
    """A hub, registered services, and the seams recorded."""

    def setUp(self):
        self.hass = harness._Hass()
        self.coord, self.client = harness._build()
        self.coord.hass = self.hass
        self.coord.cameras = [{"device_id": "cam0", "alias": "Front"},
                              {"device_id": "cam1", "alias": "Side"}]
        self.client.camera_at = lambda index: self.coord.cameras[index]
        # The format warning names the hub it is about to erase.
        self.client.host = "192.168.11.5"
        self.coord.client = self.client
        self.hass.data = {"tapo_h500": {"hubs": {"test": self.coord}}}
        self.hass.config_entries = harness._ConfigEntries([self.coord.entry])
        services.async_register(self.hass)
        self.handlers = self.hass.services.registered

    def _patch(self, name, value):
        self.addCleanup(setattr, services, name, getattr(services, name))
        setattr(services, name, value)

    def call(self, service, **data):
        data.setdefault("config_entry_id", "test")
        return asyncio.run(self.handlers[service](_Call(**data)))


class Registration(_World):
    def test_all_thirteen_answer(self):
        self.assertEqual(len(self.handlers), 13)

    def test_an_unknown_entry_is_a_validation_error(self):
        """The card sends whatever it stored; a stale entry id must read as
        "reconfigure the card", not a stack trace."""
        with self.assertRaises(ServiceValidationError):
            self.call("snooze", config_entry_id="gone")

    def test_a_bad_camera_index_is_a_validation_error(self):
        def refuse(index):
            raise ValueError("Camera index must be between 0 and 1")

        self.client.camera_at = refuse
        with self.assertRaises(ServiceValidationError):
            self.call("delete_recording", camera_index=9, start_time=NOW)


class Snooze(_World):
    def test_minutes_become_a_deadline(self):
        answer = self.call("snooze", minutes=60)
        self.assertTrue(answer["snoozed"])
        self.assertTrue(answer["until"].startswith("20"))
        self.assertEqual(self.coord.snoozed_until, NOW + 3600)

    def test_no_minutes_means_indefinite_with_no_end_to_report(self):
        answer = self.call("snooze")
        self.assertTrue(answer["snoozed"])
        self.assertIsNone(answer["until"], "infinity is not a timestamp")

    def test_zero_minutes_cancels(self):
        self.call("snooze", minutes=60)
        answer = self.call("snooze", minutes=0)
        self.assertFalse(answer["snoozed"])


class NameFace(_World):
    def test_a_name_is_written_where_everything_reads_it(self):
        answer = self.call("name_face", face_id=272465657857, name="Alice")
        self.assertEqual(self.coord.face_names, {"272465657857": "Alice"})
        self.assertEqual(answer["named"], ["272465657857"])

    def test_an_empty_name_clears_rather_than_storing_a_blank(self):
        self.call("name_face", face_id=7, name="Sam")
        answer = self.call("name_face", face_id=7, name="   ")
        self.assertEqual(self.coord.face_names, {})
        self.assertIsNone(answer["name"])

    def test_other_names_survive_an_edit(self):
        self.call("name_face", face_id=7, name="Sam")
        self.call("name_face", face_id=8, name="Alex")
        self.assertEqual(len(self.coord.face_names), 2)


class BackupAndRestore(_World):
    def test_the_backup_holds_what_a_hub_cannot_reproduce(self):
        self.call("name_face", face_id=7, name="Sam")
        answer = self.call("backup_names")
        self.assertEqual(answer["face_names"], {"7": "Sam"})

    def test_restore_merges_by_default(self):
        """The common case is an old backup onto an entry that has since
        learned more names; replace there quietly discards them."""
        self.call("name_face", face_id=8, name="Alex")
        answer = self.call("restore_names", face_names={"7": "Sam"},
                           replace=False)
        self.assertEqual(self.coord.face_names, {"7": "Sam", "8": "Alex"})
        self.assertEqual(answer["restored"], 2)

    def test_replace_replaces_when_asked(self):
        self.call("name_face", face_id=8, name="Alex")
        self.call("restore_names", face_names={"7": "Sam"}, replace=True)
        self.assertEqual(self.coord.face_names, {"7": "Sam"})

    def test_a_backup_without_a_layout_does_not_empty_the_layout(self):
        answer = self.call("restore_names", face_names={}, replace=False)
        self.assertEqual(answer["camera_order"], self.coord.camera_ranks)


class DeleteAndExport(_World):
    def test_delete_reports_what_went(self):
        async def remove(hass, camera, start):
            return ["tapo_h500/front/2026-08-30/120000.mp4"]

        self._patch("async_delete_clip", remove)
        answer = self.call("delete_recording", camera_index=0, start_time=NOW)
        self.assertEqual(len(answer["removed"]), 1)

    def test_deleting_nothing_says_so_plainly(self):
        async def remove(hass, camera, start):
            return []

        self._patch("async_delete_clip", remove)
        with self.assertRaises(ServiceValidationError):
            self.call("delete_recording", camera_index=0, start_time=NOW)

    def test_export_hands_through_to_the_copier(self):
        async def copy(hass, camera, start, destination):
            return {"copied": [f"{destination}/x.mp4"]}

        self._patch("async_export", copy)
        answer = self.call("export_recording", camera_index=0,
                           start_time=NOW, destination="/media/keep")
        self.assertEqual(answer["copied"], ["/media/keep/x.mp4"])


class FormatStorage(_World):
    def test_a_refusal_is_an_error_not_a_shrug(self):
        def refuse():
            raise OSError("-40209")

        self.client.format_storage = refuse
        with self.assertRaises(HomeAssistantError):
            self.call("format_hub_storage")

    def test_success_answers_true(self):
        self.client.format_storage = lambda: {"error_code": 0}
        self.assertEqual(self.call("format_hub_storage"),
                         {"formatted": True})


class ClassifyDownloads(_World):
    def test_totals_are_summed_across_cameras(self):
        async def classify(hass, client, camera, days):
            return {"scanned": 3, "written": 2, "days_queried": 1}

        self._patch("async_classify_downloads", classify)
        answer = self.call("classify_downloads", days=7)
        self.assertEqual(answer,
                         {"scanned": 6, "written": 4, "days_queried": 2})


class DownloadRecording(_World):
    def setUp(self):
        super().setUp()
        self.downloads = []

        async def download(hass, client, camera, start, end, convert,
                          detected=None, faces=None):
            self.downloads.append(
                {"start": start, "end": end, "convert": convert,
                 "detected": detected})
            return {"path": "x.mp4", "bytes": 1}

        self._patch("async_download_clip", download)
        self.coord.async_update_listeners = lambda: None

    def test_a_missing_end_is_looked_up_and_classification_rides_along(self):
        # Bits 1 and 5: motion (2) plus person (6), the hub's usual pair.
        self.client.recent = lambda camera, start, end: [
            {"startTime": NOW, "endTime": NOW + 15,
             "events_1": (1 << 1) | (1 << 5)}]
        self.call("download_recording", camera_index=0, start_time=NOW)
        self.assertEqual(self.downloads[0]["end"], NOW + 15)
        self.assertEqual(self.downloads[0]["detected"], [2, 6])

    def test_an_unindexed_clip_is_a_clear_refusal(self):
        self.client.recent = lambda camera, start, end: []
        with self.assertRaises(ServiceValidationError) as caught:
            self.call("download_recording", camera_index=0, start_time=NOW)
        self.assertIn("still be recording", str(caught.exception))

    def test_a_backwards_window_is_refused(self):
        with self.assertRaises(ServiceValidationError):
            self.call("download_recording", camera_index=0,
                      start_time=NOW, end_time=NOW - 5)

    def test_the_mp4_default_comes_from_the_options(self):
        self.coord.entry.options = {**self.coord.entry.options,
                                    "convert_mp4": False}
        self.call("download_recording", camera_index=0,
                  start_time=NOW, end_time=NOW + 15)
        self.assertIs(self.downloads[0]["convert"], False)


class DescribeRecording(_World):
    def setUp(self):
        super().setUp()
        self.thumb = Path(__file__).parent / "_svc_thumb.jpg"
        self.thumb.write_bytes(b"jpeg")
        self.addCleanup(self.thumb.unlink)
        self._patch("clip_path",
                    lambda hass, camera, start, suffix: self.thumb)
        self._patch("media_content_id", lambda hass, path: "media://x")

    def test_no_thumbnail_yet_is_explained_not_crashed(self):
        self._patch("clip_path", lambda *a: Path("/nowhere/x.jpg"))
        with self.assertRaises(ServiceValidationError) as caught:
            self.call("describe_recording", camera_index=0, start_time=NOW,
                      agent_id="ai_task.gpt", prompt="what is this")
        self.assertIn("thumbnail", str(caught.exception).lower())

    def test_an_ai_task_agent_gets_the_picture_attached(self):
        self.hass.services.available.add(("ai_task", "generate_data"))
        self.hass.services.responses[("ai_task", "generate_data")] = {
            "data": "A person at the door"}
        answer = self.call("describe_recording", camera_index=0,
                           start_time=NOW, agent_id="ai_task.gpt",
                           prompt="what is this")
        self.assertEqual(answer["description"], "A person at the door")
        domain, service, data = self.hass.services.calls[0]
        self.assertEqual((domain, service), ("ai_task", "generate_data"))
        self.assertEqual(data["attachments"][0]["media_content_id"],
                         "media://x")

    def test_a_conversation_agent_is_the_fallback(self):
        self.hass.services.available.add(("conversation", "process"))
        self.hass.services.responses[("conversation", "process")] = {
            "response": {"speech": {"plain": {"speech": "A cat"}}}}
        answer = self.call("describe_recording", camera_index=0,
                           start_time=NOW, agent_id="conversation.claude",
                           prompt="what is this")
        self.assertEqual(answer["description"], "A cat")

    def test_no_ai_at_all_is_said_plainly(self):
        with self.assertRaises(HomeAssistantError) as caught:
            self.call("describe_recording", camera_index=0, start_time=NOW,
                      agent_id="ai_task.gpt", prompt="x")
        self.assertIn("No AI service", str(caught.exception))


class DailySummary(_World):
    def test_one_sentence_per_camera_and_usually_no_highlights(self):
        """Both cameras busy in the ordinary way: the digest names them and
        flags nothing, because a list that always has something in it says
        nothing."""
        self.coord.data = {"clips": {0: [clip(NOW - 600)],
                                     1: [clip(NOW - 900)]}}
        answer = self.call("daily_summary", hours=24)
        self.assertEqual(answer["hours"], 24)
        self.assertIn("Front", answer["summary"])
        self.assertEqual(answer["highlights"], [])

    def test_a_camera_that_recorded_nothing_is_the_highlight(self):
        """What was different is what a digest is for -- and a silent camera
        is different."""
        self.coord.data = {"clips": {0: [clip(NOW - 600)], 1: []}}
        answer = self.call("daily_summary", hours=24)
        self.assertEqual(answer["highlights"], ["Side recorded nothing"])

    def test_two_cameras_sharing_a_name_are_told_apart(self):
        self.coord.cameras = [{"device_id": "a", "alias": "Door"},
                              {"device_id": "b", "alias": "Door"}]
        self.coord.data = {"clips": {0: [clip(NOW - 600)], 1: []}}
        answer = self.call("daily_summary", hours=24)
        self.assertIn("camera 0", answer["summary"])


class FindFace(_World):
    def setUp(self):
        super().setUp()
        self._patch("existing_clip", lambda hass, camera, start: None)
        self._patch("archive_face_search", lambda hass, camera, ids: [])
        self.call("name_face", face_id=272465657857, name="Alice")
        self.call("name_face", face_id=272465657858, name="Alice")

    def test_a_name_finds_every_cluster_wearing_it(self):
        """The hub hands the same person several ids as the light changes;
        naming both is what says they are one person."""
        self.coord.data = {"clips": {
            0: [clip(NOW - 60, faces=[272465657857])],
            1: [clip(NOW - 30, faces=[272465657858])]}}
        answer = self.call("find_face", who="alice")
        self.assertEqual(answer["count"], 2)
        self.assertEqual(answer["recordings"][0]["start_time"], NOW - 30,
                         "newest first")

    def test_an_unnamed_id_can_be_searched_raw(self):
        self.coord.data = {"clips": {0: [clip(NOW - 60, faces=[999])]}}
        answer = self.call("find_face", who="999")
        self.assertEqual(answer["count"], 1)

    def test_the_archive_fills_in_beyond_the_hubs_day(self):
        self.coord.data = {"clips": {
            0: [clip(NOW - 60, faces=[272465657857])]}}
        self._patch("archive_face_search", lambda hass, camera, ids: [
            {"start_time": NOW - 90000, "detection_types": [2, 6]}])
        answer = self.call("find_face", who="Alice")
        self.assertEqual(answer["count"], 3, "one live, one archived per camera")
        self.assertTrue(all(item["downloaded"]
                            for item in answer["recordings"]
                            if item["end_time"] is None))

    def test_a_live_hit_is_not_double_counted_from_its_sidecar(self):
        self.coord.data = {"clips": {
            0: [clip(NOW - 60, faces=[272465657857])], 1: []}}
        self._patch("archive_face_search", lambda hass, camera, ids: [
            {"start_time": NOW - 60, "detection_types": [2]}])
        answer = self.call("find_face", who="Alice")
        starts = [item["start_time"] for item in answer["recordings"]
                  if item["camera_index"] == 0]
        self.assertEqual(starts.count(NOW - 60), 1)


class ListRecordings(_World):
    def setUp(self):
        super().setUp()
        self.windows = []

        def recordings(index, start_date, end_date):
            self.windows.append((start_date, end_date))
            return self.coord.cameras[index], [clip(NOW - 60),
                                               clip(NOW - 3600)]

        self.client.recordings = recordings
        self._patch("scan_downloaded",
                    lambda hass, camera, starts: {NOW - 3600: Path("x.mp4")})
        self._patch("describe", lambda hass, path: {"thumbnail": "/local/x.jpg"})
        self._patch("preview_url",
                    lambda hass, entry_id, index, start: f"/preview/{start}")

    def test_no_window_asked_for_follows_the_configured_days(self):
        self.coord.entry.options = {**self.coord.entry.options,
                                    "card_days": 3}
        answer = self.call("list_recordings", camera_index=0)
        self.assertEqual(answer["days"], 3)
        self.assertEqual(len(self.windows), 1)

    def test_an_explicit_window_is_respected_and_uncaptioned(self):
        answer = self.call("list_recordings", camera_index=0,
                           start_date="20260828", end_date="20260830")
        self.assertIsNone(answer["days"])
        self.assertEqual(self.windows, [("20260828", "20260830")])

    def test_downloaded_clips_carry_their_file_and_the_rest_a_preview(self):
        answer = self.call("list_recordings", camera_index=0)
        by_start = {item["start_time"]: item
                    for item in answer["recordings"]}
        self.assertTrue(by_start[NOW - 3600]["downloaded"])
        self.assertEqual(by_start[NOW - 3600]["thumbnail"], "/local/x.jpg")
        self.assertFalse(by_start[NOW - 60]["downloaded"])
        self.assertEqual(by_start[NOW - 60]["thumbnail"],
                         f"/preview/{NOW - 60}")

    def test_the_listing_is_oldest_first_with_the_names_riding_along(self):
        self.call("name_face", face_id=7, name="Sam")
        answer = self.call("list_recordings", camera_index=0)
        starts = [item["start_time"] for item in answer["recordings"]]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(answer["face_names"], {"7": "Sam"})
        self.assertEqual([c["index"] for c in answer["cameras"]], [0, 1])


if __name__ == "__main__":
    unittest.main()
