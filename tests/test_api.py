"""Runs without pytapo or Home Assistant installed.

pytapo is stubbed and the component is loaded as a package whose __init__ is
never executed, so these cover the hub-facing logic without pulling in the
Home Assistant runtime.
"""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"


class _StubSession:
    async def _send_http_request(self, delimiter, headers):
        return None


class _StubAESHelper:
    """Mimics pytapo's guard: any falsy nonce is rejected outright."""

    seen = None

    def __init__(self, username, nonce, cloud_password, super_secret_key,
                 encryptionMethod):
        if not nonce:
            raise ValueError("Nonce is missing from key exchange")
        _StubAESHelper.seen = nonce


def _install_stubs():
    pytapo = types.ModuleType("pytapo")
    pytapo.Tapo = type("Tapo", (), {})
    media_stream = types.ModuleType("pytapo.media_stream")
    session = types.ModuleType("pytapo.media_stream.session")
    session.HttpMediaSession = _StubSession
    crypto = types.ModuleType("pytapo.media_stream.crypto")
    crypto.AESHelper = _StubAESHelper
    media_stream.session = session
    media_stream.crypto = crypto
    sys.modules.update({
        "pytapo": pytapo,
        "pytapo.media_stream": media_stream,
        "pytapo.media_stream.session": session,
        "pytapo.media_stream.crypto": crypto,
    })
    package = types.ModuleType("tapo_h500")
    package.__path__ = [str(COMPONENT)]
    sys.modules["tapo_h500"] = package


_install_stubs()
api = importlib.import_module("tapo_h500.api")
clips = importlib.import_module("tapo_h500.clips")
H500MediaSession = api.H500MediaSession
H500Client = api.H500Client
IncompleteRecordingError = api.IncompleteRecordingError
build_download_payload = api.build_download_payload

CAMERA = {"device_id": "child", "mac": "AABB", "channel_id": 0}


class ApiTest(unittest.TestCase):
    def test_payload_matches_verified_h500_shape(self):
        payload = build_download_payload(CAMERA, 10, 20, "player", 1)
        self.assertEqual(payload["params"]["download"], {
            "dev_id": "child", "mac": "AABB", "channels": [0],
            "client_id": 1, "end_time": "20", "media_type": 0,
            "start_time": "10", "player_id": "player",
        })

    def test_initial_post_forces_zero_content_length(self):
        session = object.__new__(H500MediaSession)
        with patch.object(
            _StubSession, "_send_http_request", new=AsyncMock()
        ) as parent_send:
            asyncio.run(session._send_http_request(
                b"POST /stream HTTP/1.1", {b"Content-Length": b"-1"}))
        self.assertEqual(parent_send.call_args.args[1][b"Content-Length"], b"0")

    def test_stock_session_uses_verified_ack_window(self):
        client = H500Client("host", "admin", "local", "cloud")
        client._super_secret_key = ""
        client._encryption_method = object()

        class FakeSession:
            kwargs = None

            def __init__(self, **kwargs):
                FakeSession.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def transceive(self, *_, **__):
                if False:
                    yield None

        async def consume():
            with patch.object(api, "H500MediaSession", FakeSession):
                return [part async for part in client.iter_recording(CAMERA, 10, 20)]

        with self.assertRaises(IncompleteRecordingError):
            asyncio.run(consume())
        self.assertEqual(FakeSession.kwargs["window_size"], 25)

    def test_finished_notification_completes_stream(self):
        client = H500Client("host", "admin", "local", "cloud")
        client._super_secret_key = ""
        client._encryption_method = object()

        class Response:
            mimetype = "application/json"
            plaintext = (b'{"type":"notification","params":'
                         b'{"event_type":"stream_status","status":"finished"}}')

        class FakeSession:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def transceive(self, *_, **__):
                yield Response()

        async def consume_finished():
            with patch.object(api, "H500MediaSession", FakeSession):
                return [part async for part in client.iter_recording(CAMERA, 10, 20)]

        self.assertEqual(asyncio.run(consume_finished()), [])

    def test_unsupported_detection_search_disables_itself(self):
        client = H500Client("host", "admin", "local", "cloud")

        class Hub:
            calls = 0

            def executeFunction(self, *_, **__):
                Hub.calls += 1
                raise RuntimeError("-40106 method not supported")

        client._hub = Hub()
        self.assertIsNone(client.detections(CAMERA, 0, 10))
        self.assertIsNone(client.detections(CAMERA, 0, 10))
        self.assertEqual(Hub.calls, 1)


class EmptyNonceTest(unittest.TestCase):
    """The H500 reports media encryption on, then sends nonce="".

    pytapo rejects any falsy nonce, which broke every download. An empty nonce
    is still usable — the hub derives the same key from it — so it has to reach
    the key derivation intact rather than being rejected or substituted.
    """

    def test_empty_nonce_is_truthy_but_still_empty(self):
        nonce = api._EmptyNonce()
        self.assertTrue(nonce)
        self.assertEqual(nonce, b"")
        self.assertEqual(len(nonce), 0)
        # Key derivation must see an empty nonce, not a placeholder.
        self.assertEqual(nonce + b":" + b"PWD", b":PWD")

    def test_empty_nonce_survives_the_guard(self):
        api.H500AESHelper("admin", b"", "cloud", "", object())
        self.assertEqual(_StubAESHelper.seen, b"")
        self.assertTrue(_StubAESHelper.seen)

    def test_real_nonce_is_passed_through_untouched(self):
        api.H500AESHelper("admin", b"abc123", "cloud", "", object())
        self.assertEqual(_StubAESHelper.seen, b"abc123")

    def test_session_module_uses_the_patched_helper(self):
        from pytapo.media_stream import session as patched
        self.assertIs(patched.AESHelper, api.H500AESHelper)


class ClipsTest(unittest.TestCase):
    def test_ring_labels_are_recognised(self):
        for label in ("doorbell_ring", "RING", "button press", "visitor"):
            self.assertEqual(clips.event_type({"video_type": label}), "ring")

    def test_unknown_and_missing_labels_are_motion(self):
        self.assertEqual(clips.event_type({"video_type": "pir"}), "motion")
        self.assertEqual(clips.event_type({}), "motion")

    def test_both_timestamp_spellings_are_accepted(self):
        self.assertEqual(clips.start_of({"startTime": "10"}), 10)
        self.assertEqual(clips.start_of({"start_time": 10}), 10)
        self.assertEqual(clips.end_of({"endTime": 20}), 20)
        self.assertIsNone(clips.start_of({"startTime": "later"}))
        self.assertIsNone(clips.end_of({}))

    def test_camera_slug_cannot_escape_the_media_directory(self):
        self.assertEqual(clips.camera_slug({"alias": "Side Doorbell"}), "side_doorbell")
        self.assertEqual(clips.camera_slug({"alias": "../../bad"}), "bad")
        self.assertEqual(clips.camera_slug({"alias": "/"}), "camera")
        self.assertEqual(clips.camera_slug({}), "camera")
        self.assertEqual(len(clips.camera_slug({"alias": "x" * 200})), 60)

    def test_flatten_skips_anything_that_is_not_a_clip(self):
        result = {"playback": {"search_video_results": [
            {"0": {"startTime": 10, "endTime": 20}, "count": 1},
            {"0": {"no_start": True}},
            "junk",
        ]}}
        self.assertEqual(clips.flatten_clips(result), [{"startTime": 10, "endTime": 20}])


const = importlib.import_module("tapo_h500.const")
status = importlib.import_module("tapo_h500.status")

# Verbatim from an H500 on firmware 1.3.20, with the address replaced.
OBSERVED = {
    "getSdCardStatus": {"harddisk_manage": {"hd_info": [{"hd_info_1": {
        "disk_name": "1", "loop_record_status": "1", "rw_attr": "rw",
        "total_space": "10.00 GB", "write_protect": "0", "type": "local",
        "status": "normal", "detect_status": "failed", "percent": "100",
        "free_space": "8.12 GB", "video_total_space": "9.50 GB",
        "video_free_space": "7.62 GB"}}]}},
    "getSirenStatus": {"status": "off", "time_left": 0},
    "getFirmwareUpdateStatus": {"cloud_config": {"upgrade_status": {
        "state": "normal", "lastUpgradingSuccess": True}}},
    "getLedStatus": {"led": {"config": {
        ".name": "config", ".type": "led", "enabled": "on"}}},
    "getCircularRecordingConfig": {"harddisk_manage": {"harddisk": {"loop": "on"}}},
    "getMediaEncrypt": {"cet": {"media_encrypt": {"enabled": "on"}}},
    "getDeviceIpAddress": {"network": {"wan": {"ipaddr": "192.168.1.50"}}},
}


class RetentionTest(unittest.TestCase):
    """Whatever surplus() returns gets deleted, so the edges matter."""

    def test_keeps_the_newest_and_drops_the_rest(self):
        items = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertEqual(clips.surplus(items, 5), [1, 2, 3, 4, 5])
        # An eleventh arriving evicts exactly one more.
        self.assertEqual(clips.surplus(items + [11], 5), [1, 2, 3, 4, 5, 6])

    def test_zero_or_negative_means_no_limit(self):
        items = [1, 2, 3]
        self.assertEqual(clips.surplus(items, 0), [])
        self.assertEqual(clips.surplus(items, -1), [])

    def test_never_deletes_when_at_or_under_the_limit(self):
        self.assertEqual(clips.surplus([1, 2, 3], 3), [])
        self.assertEqual(clips.surplus([1, 2], 3), [])
        self.assertEqual(clips.surplus([], 3), [])

    def test_a_limit_of_one_keeps_only_the_newest(self):
        self.assertEqual(clips.surplus([1, 2, 3], 1), [1, 2])

    def test_the_input_is_not_mutated(self):
        items = [1, 2, 3, 4]
        clips.surplus(items, 2)
        self.assertEqual(items, [1, 2, 3, 4])


class ConvertArgsTest(unittest.TestCase):
    def test_output_format_is_explicit(self):
        """The clip is written to a ".mp4.part" temporary file first.

        ffmpeg chooses its muxer from the extension and does not know ".part",
        so without an explicit format every conversion fails with "Unable to
        choose an output format" and no download ever completes.
        """
        args = const.CONVERT_ARGS
        self.assertIn("-f", args)
        self.assertEqual(args[args.index("-f") + 1], "mp4")

    def test_thumbnails_are_scaled_down(self):
        """A full frame is 2304x1296 and about 530 KB; the card shows 96x54."""
        args = const.THUMBNAIL_ARGS
        self.assertIn("scale=640:-2", args)
        self.assertEqual(args[args.index("-frames:v") + 1], "1")

    def test_video_is_copied_not_reencoded(self):
        args = const.CONVERT_ARGS
        self.assertEqual(args[args.index("-c:v") + 1], "copy")
        self.assertIn("+faststart", args)


class StatusTest(unittest.TestCase):
    def test_readings_from_a_real_response(self):
        r = status.hub_readings(OBSERVED)
        self.assertEqual(r["storage_free_gb"], 7.62)
        self.assertEqual(r["storage_total_gb"], 9.5)
        self.assertEqual(r["storage_used_percent"], 19.8)
        self.assertTrue(r["storage_healthy"])
        self.assertFalse(r["siren_active"])
        self.assertEqual(r["firmware_state"], "normal")
        self.assertTrue(r["led_on"])
        self.assertTrue(r["loop_recording"])
        self.assertTrue(r["media_encrypted"])
        self.assertEqual(r["ip_address"], "192.168.1.50")

    def test_missing_data_yields_none_not_an_exception(self):
        r = status.hub_readings({})
        self.assertIsNone(r["storage_free_gb"])
        self.assertIsNone(r["storage_used_percent"])
        self.assertIsNone(r["storage_healthy"])
        self.assertIsNone(r["siren_active"])
        self.assertIsNone(r["led_on"])

    def test_sizes_parse_across_units(self):
        self.assertEqual(status.gigabytes("7.62 GB"), 7.62)
        self.assertEqual(status.gigabytes("1024 MB"), 1.0)
        self.assertEqual(status.gigabytes("2 TB"), 2048.0)
        self.assertIsNone(status.gigabytes("plenty"))
        self.assertIsNone(status.gigabytes(None))

    def test_siren_on_is_detected(self):
        r = status.hub_readings({"getSirenStatus": {"status": "on", "time_left": 12}})
        self.assertTrue(r["siren_active"])
        self.assertEqual(r["siren_time_left"], 12)

    def test_unpack_keeps_only_successful_subresponses(self):
        packed = {"result": {"responses": [
            {"method": "a", "result": {"x": 1}, "error_code": 0},
            {"method": "b", "error_code": -40106},
            {"method": "c", "result": {}, "error_code": 0},
        ]}}
        self.assertEqual(status.unpack_multiple(packed), {"a": {"x": 1}})
        self.assertEqual(status.unpack_multiple("junk"), {})

    def test_disk_handles_a_malformed_table(self):
        for broken in ({}, {"getSdCardStatus": {}},
                       {"getSdCardStatus": {"harddisk_manage": {"hd_info": []}}},
                       {"getSdCardStatus": {"harddisk_manage": {"hd_info": ["x"]}}}):
            self.assertEqual(status.disk(broken), {})


class _FakeHub:
    """Records executeFunction calls instead of talking to a hub."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result or {}

    def executeFunction(self, method, params):
        self.calls.append((method, params))
        return self.result


class SirenTest(unittest.TestCase):
    """Siren control, verified against firmware 1.3.20.

    getSirenStatus/Config/TypeList answer and setSirenStatus/setSirenConfig are
    accepted with error_code 0. Volume is 1-10; 0 and 11 return -40209.
    """

    def _client(self, result=None):
        client = H500Client("host", "admin", "local", "cloud")
        client._hub = _FakeHub(result)
        return client

    def test_volume_scales_and_never_leaves_the_range_the_hub_accepts(self):
        self.assertEqual(status.hub_volume(1.0), 10)
        self.assertEqual(status.hub_volume(0.8), 8)
        # 0.0 is a level Home Assistant will send; 0 is rejected by the hub.
        self.assertEqual(status.hub_volume(0.0), 1)
        for level in (-5.0, 0.0, 0.5, 1.0, 99.0):
            self.assertIn(status.hub_volume(level), range(1, 11))

    def test_turning_the_siren_on_and_off_uses_the_siren_namespace(self):
        client = self._client()
        client.set_siren(True)
        client.set_siren(False)
        self.assertEqual(client._hub.calls, [
            ("setSirenStatus", {"siren": {"status": "on"}}),
            ("setSirenStatus", {"siren": {"status": "off"}}),
        ])

    def test_config_sends_only_the_fields_that_changed(self):
        client = self._client()
        client.set_siren_config(volume=7)
        self.assertEqual(client._hub.calls[-1],
                         ("setSirenConfig", {"siren": {"volume": "7"}}))
        client.set_siren_config(tone="Alarm 1", duration=30)
        self.assertEqual(
            client._hub.calls[-1],
            ("setSirenConfig", {"siren": {"siren_type": "Alarm 1", "duration": 30}}))

    def test_an_empty_config_change_is_not_sent_at_all(self):
        client = self._client()
        self.assertIsNone(client.set_siren_config())
        self.assertEqual(client._hub.calls, [])

    def test_tone_list_survives_a_junk_entry(self):
        client = self._client({"siren_type_list": ["Alarm 1", 7, None, "Alarm 2"]})
        self.assertEqual(client.siren_tones(), ["Alarm 1", "Alarm 2"])
        self.assertEqual(self._client({}).siren_tones(), [])

    def test_readings_expose_the_config_the_hub_returns_as_strings(self):
        readings = status.hub_readings({
            "getSirenStatus": {"status": "off", "time_left": 0},
            "getSirenConfig": {"siren_type": "Doorbell Ring 5",
                               "volume": "8", "duration": 300},
        })
        self.assertEqual(readings["siren_tone"], "Doorbell Ring 5")
        self.assertEqual(readings["siren_volume"], 8)
        self.assertEqual(readings["siren_duration"], 300)
        self.assertFalse(readings["siren_active"])

    def test_missing_siren_config_does_not_break_the_poll(self):
        readings = status.hub_readings({})
        for key in ("siren_tone", "siren_volume", "siren_duration"):
            self.assertIsNone(readings[key])


class ClockAndAudioTest(unittest.TestCase):
    """dateTime and usrDefAudio, both verbatim from firmware 1.3.20."""

    CLOCK = {"getClockStatus": {"system": {"clock_status": {
        "seconds_from_1970": 1786585660, "local_time": "2026-08-12 21:47:40"}}}}
    ZONE = {"getTimezone": {"system": {"basic": {
        "zone_id": "America/New_York", "timezone": "UTC-05:00"}}}}

    def test_clock_offset_is_signed(self):
        # Ahead and behind are different faults; collapsing them would hide a
        # hub drifting one way.
        self.assertEqual(status.clock_offset(1786585660, 1786585640), 20)
        self.assertEqual(status.clock_offset(1786585620, 1786585640), -20)
        self.assertEqual(status.clock_offset(1786585640, 1786585640), 0)

    def test_a_hub_that_gives_no_clock_reads_none_not_zero(self):
        # Zero would claim perfect sync from a hub that said nothing.
        self.assertIsNone(status.clock_offset(None))
        self.assertIsNone(status.clock_offset(""))
        self.assertIsNone(status.hub_readings({})["clock_offset"])

    def test_readings_carry_the_clock_and_zone(self):
        readings = status.hub_readings({**self.CLOCK, **self.ZONE},
                                       now=1786585660)
        self.assertEqual(readings["clock_offset"], 0)
        self.assertEqual(readings["hub_local_time"], "2026-08-12 21:47:40")
        self.assertEqual(readings["timezone"], "America/New_York")

    def test_empty_audio_slots_are_not_counted(self):
        # All five slots always come back; the empty ones carry empty strings
        # rather than being absent, so presence proves nothing.
        empty = {"getUsrDefAudioList": {"usr_def_audio": {
            f"file_{n}": {"file_id": "", "name": "", "index": "", "duration": ""}
            for n in range(1, 6)}}}
        self.assertEqual(status.used_audio_slots(empty), [])
        self.assertEqual(status.hub_readings(empty)["custom_sounds"], 0)

    def test_named_audio_slots_are_counted_in_order(self):
        filled = {"getUsrDefAudioList": {"usr_def_audio": {
            "file_1": {"name": "Front gate"},
            "file_2": {"name": ""},
            "file_3": {"name": "Delivery"},
        }}}
        self.assertEqual(status.used_audio_slots(filled),
                         ["Front gate", "Delivery"])
        self.assertEqual(status.hub_readings(filled)["custom_sounds"], 2)

    def test_a_malformed_audio_list_does_not_break_the_poll(self):
        for junk in ({"getUsrDefAudioList": {}},
                     {"getUsrDefAudioList": {"usr_def_audio": "nonsense"}},
                     {"getUsrDefAudioList": {"usr_def_audio": {"file_1": "x"}}},
                     {}):
            self.assertEqual(status.used_audio_slots(junk), [])

    def test_face_detection_reading(self):
        readings = status.hub_readings({"getFaceDetectionConfig": {
            "face_detection": {"detection": {
                "enabled": "on", "tags": ["family", "courier"]}}}})
        self.assertTrue(readings["face_detection"])
        self.assertEqual(readings["face_detection_tags"], ["family", "courier"])
        self.assertIsNone(status.hub_readings({})["face_detection"])


class RecordingWindowTest(unittest.TestCase):
    """The requested range must be searched, whatever dates the hub volunteers.

    Regression for evening clips vanishing from the card. The hub reports the
    dates it holds video for in its OWN local time, and those were being read
    back as UTC dates and turned into UTC midnight windows. On a hub at UTC-4
    that loses every clip between 8pm and midnight local, because they fall on
    the next UTC date -- one the hub never names.
    """

    # 21:16 on a UTC-4 hub. The clip the user noticed was missing.
    EVENING_CLIP = 1786583783            # 2026-08-13 01:16:23 UTC

    class _Hub:
        """Answers like the real hub: local dates, and clips by epoch."""

        def __init__(self, clips):
            self.clips = clips
            self.searched = []

        def executeFunction(self, method, params):
            if method == "searchDateWithVideo":
                # Verbatim behaviour: its own local dates, and it ignores the
                # range it was asked for.
                return {"playback": {"search_results": [
                    {"r1": {"date": "20260811"}}, {"r2": {"date": "20260812"}}]}}
            if method == "searchVideoWithUTC":
                block = params["playback"]["search_video_with_utc"]
                low, high = int(block["start_time"]), int(block["end_time"])
                self.searched.append((low, high))
                return {"playback": {"search_video_results": [
                    {f"r{i}": {"startTime": t, "endTime": t + 15}}
                    for i, t in enumerate(self.clips) if low <= t <= high]}}
            return {}

    def _client(self):
        client = H500Client("host", "admin", "local", "cloud")
        client._hub = self._Hub([1786520000, self.EVENING_CLIP])
        client.cameras = lambda: [{"device_id": "c", "mac": "m"}]
        client.camera_at = lambda index: {"device_id": "c", "mac": "m"}
        client.detections = lambda *a, **k: []
        return client

    def test_an_evening_clip_is_not_lost(self):
        _, found = self._client().recordings(0, "20260813", "20260813")
        starts = [clips.start_of(clip) for clip in found]
        self.assertIn(self.EVENING_CLIP, starts,
                      "a 21:16 local clip fell outside every searched window")

    def test_the_whole_requested_range_is_searched(self):
        client = self._client()
        client.recordings(0, "20260812", "20260813")
        covered = client._hub.searched
        self.assertTrue(covered, "nothing was searched at all")
        low = min(a for a, _ in covered)
        high = max(b for _, b in covered)
        # 20260812 00:00:00 UTC through the last second of 20260813.
        self.assertLessEqual(low, 1786492800)
        self.assertGreaterEqual(high, 1786665599)

    def test_the_hubs_own_date_list_does_not_narrow_the_search(self):
        # The hub only ever names 0811 and 0812; asking for 0813 must still
        # search 0813.
        client = self._client()
        client.recordings(0, "20260813", "20260813")
        self.assertTrue(any(b >= self.EVENING_CLIP for _, b in client._hub.searched),
                        "the search stopped short of the requested date")


class DetectionTest(unittest.TestCase):
    """What actually triggered a recording.

    Every record below is verbatim from an H500 on firmware 1.3.20. The clip
    index classifies nothing -- video_type is "2" for all 26 clips in a
    three-day window -- and the detection log is what carries the type.
    """

    # Real detections, straight off the hub.
    REAL = [
        {"alarm_type": 2, "events_1": 2, "start_time": 1786553183},
        {"alarm_type": 6, "events_1": 34, "start_time": 1786553786},
        {"alarm_type": 8, "events_1": 130, "start_time": 1786570673},
        {"alarm_type": 9, "events_1": 256, "start_time": 1786570846},
        {"alarm_type": 17, "events_1": 66050, "start_time": 1786583783},
        {"alarm_type": 19, "events_1": 262178, "start_time": 1786552988},
        {"alarm_type": 20, "events_1": 524450, "start_time": 1786542131,
         "event_info": [{"face_bitmap": 0, "face_id": 272465657857}]},
        {"alarm_type": 22, "events_1": 2097442, "start_time": 1786570781},
    ]

    def test_alarm_type_is_the_highest_bit_of_the_mask_plus_one(self):
        # This is the whole basis for reading events_1 as a bitmask, so it is
        # asserted against every record rather than assumed.
        for record in self.REAL:
            self.assertEqual(
                clips.detection_types(record)[-1], record["alarm_type"],
                f"events_1={record['events_1']}")

    def test_the_mask_lists_everything_that_fired_at_once(self):
        # 2097442 = bits 1, 5, 8 and 21.
        self.assertEqual(
            clips.detection_types({"events_1": 2097442, "alarm_type": 22}),
            [2, 6, 9, 22])

    def test_a_detection_with_no_mask_falls_back_to_alarm_type(self):
        self.assertEqual(clips.detection_types({"alarm_type": 22}), [22])
        self.assertEqual(clips.detection_types({}), [])

    def test_unknown_codes_are_shown_not_guessed(self):
        # Naming an unproven code would put a confident wrong label on a
        # recording, which is worse than showing the number.
        # 6 and 9 are named now (person, pet); 22 still is not.
        self.assertEqual(
            clips.describe_detection({"events_1": 2097442, "alarm_type": 22}),
            "motion + person + pet + type 22")
        self.assertEqual(clips.describe_detection({"events_1": 2}), "motion")
        self.assertIsNone(clips.describe_detection({}))

    def test_the_only_code_seen_with_a_face_id_is_named_face(self):
        face = next(r for r in self.REAL if r["alarm_type"] == 20)
        self.assertIn("face", clips.describe_detection(face))

    def test_face_ids_come_off_the_detection(self):
        # Verbatim: the hub gives a number and nothing else. There is no face
        # library to resolve it against -- getFaceList and friends are -40106.
        face = next(r for r in self.REAL if r["alarm_type"] == 20)
        self.assertEqual(clips.face_ids(face), [272465657857])

    def test_face_ids_are_deduplicated_and_absent_when_there_are_none(self):
        twice = {"event_info": [{"face_id": 5}, {"face_id": 5}, {"face_id": 9}]}
        self.assertEqual(clips.face_ids(twice), [5, 9])
        for empty in ({}, {"event_info": []}, {"event_info": None},
                      {"event_info": ["junk"]}, {"event_info": [{}]}):
            self.assertEqual(clips.face_ids(empty), [])

    # The real press: the front doorbell was rung at 14:42:25 on 2026-08-13 and
    # this was the only event on that camera in six hours.
    PRESS = {"alarm_type": 17, "events_1": 66080, "start_time": 1786646545}

    def test_a_real_doorbell_press_classifies_as_a_ring(self):
        self.assertEqual(clips.event_type(self.PRESS), "ring")
        self.assertEqual(clips.detection_types(self.PRESS), [6, 10, 17])
        self.assertIn("doorbell", clips.describe_detection(self.PRESS))

    def test_motion_is_still_not_a_ring(self):
        for record in self.REAL:
            expected = "ring" if record["alarm_type"] == 17 else "motion"
            self.assertEqual(clips.event_type(record), expected,
                             f"alarm_type {record['alarm_type']}")

    # The Tapo app's own labels for three side-doorbell events on 2026-08-12,
    # which is what names vehicle and pet: they differ by exactly one code.
    LABELLED = [
        ("motion + person",        2097186, {"motion", "person"}),
        ("person + motion + car",  2097314, {"motion", "person", "vehicle"}),
        ("person + dog + motion",  2097442, {"motion", "person", "pet"}),
    ]

    def test_the_apps_own_labels_come_back_out(self):
        for label, events_1, expected in self.LABELLED:
            described = clips.describe_detection({"events_1": events_1})
            named = {w for w in described.replace(" + ", ",").split(",")
                     if not w.startswith("type ")}
            self.assertEqual(named, expected, f"app said {label!r}, got {described!r}")

    def test_the_car_and_the_dog_are_the_only_difference(self):
        base = set(clips.detection_types({"events_1": 2097186}))
        self.assertEqual(set(clips.detection_types({"events_1": 2097314})) - base, {8})
        self.assertEqual(set(clips.detection_types({"events_1": 2097442})) - base, {9})

    def test_lifting_the_camera_off_its_mount_is_a_theft_event(self):
        # The front camera was removed at 11:16:16 on 2026-08-13. Person and
        # face ride along because someone was standing there doing it.
        theft = {"alarm_type": 19, "events_1": 786464}
        self.assertEqual(clips.detection_types(theft), [6, 19, 20])
        self.assertEqual(clips.describe_detection(theft), "person + theft + face")
        # A tamper alarm is not a doorbell press.
        self.assertEqual(clips.event_type(theft), "motion")

    def test_unattributed_codes_are_still_shown_as_numbers(self):
        # 22 is common but nothing observed says what it means; naming it would
        # print a guess onto every recording that carries it.
        self.assertNotIn(22, clips.DETECTION_NAMES)
        self.assertIn("type 22", clips.describe_detection({"events_1": 2097152}))

    def test_detections_are_matched_to_clips_by_start_time(self):
        clip_list = [{"startTime": 1786553183, "endTime": 1786553199},
                     {"startTime": 1786542132, "endTime": 1786542147}]
        clips.attach_detections(clip_list, self.REAL)
        self.assertEqual(clip_list[0]["alarm_type"], 2)
        # One second out still matches: the clip index and the detection log
        # are separate lookups and need not agree to the second.
        self.assertEqual(clip_list[1]["alarm_type"], 20)

    def test_a_clip_with_no_detection_is_left_alone(self):
        clip_list = [{"startTime": 1, "endTime": 2}]
        clips.attach_detections(clip_list, self.REAL)
        self.assertNotIn("alarm_type", clip_list[0])
        clips.attach_detections(clip_list, [])
        clips.attach_detections(clip_list, None)
        self.assertNotIn("alarm_type", clip_list[0])

    def test_an_empty_window_must_not_disable_the_call(self):
        """The bug that made this look dead for the life of a session."""
        client = H500Client("host", "admin", "local", "cloud")
        client._hub = _FakeHub({})          # a quiet window answers {}
        self.assertEqual(client.detections({"device_id": "c", "mac": "m"}, 0, 1), [])
        self.assertTrue(client._detection_supported, "one quiet poll disabled it")
        client._hub = _FakeHub({"playback": {"search_detection_list": self.REAL}})
        self.assertEqual(len(client.detections({"device_id": "c", "mac": "m"}, 0, 1)), 8)


class HubSettingsTest(unittest.TestCase):
    """Writable hub settings.

    Each setter was proven on firmware 1.3.20 by writing the hub's own current
    value back and confirming error_code 0 with the setting unchanged.
    """

    def _client(self):
        client = H500Client("host", "admin", "local", "cloud")
        client._hub = _FakeHub()
        return client

    def test_each_toggle_uses_the_namespace_the_hub_accepted(self):
        client = self._client()
        client.set_led(True)
        client.set_loop_recording(False)
        client.set_diagnose_mode(True)
        self.assertEqual(client._hub.calls, [
            ("setLedStatus", {"led": {"config": {"enabled": "on"}}}),
            ("setCircularRecordingConfig",
             {"harddisk_manage": {"harddisk": {"loop": "off"}}}),
            ("setDiagnoseMode", {"system": {"sys": {"diagnose_mode": "on"}}}),
        ])

    def test_auto_upgrade_toggle_keeps_the_schedule(self):
        """The hub replaces `common` wholesale, so a bare enabled wipes it."""
        readings = {"auto_upgrade_config": {
            "enabled": "on", "time": "03:00", "random_range": 120}}
        self.assertEqual(status.auto_upgrade_config(readings, False), {
            "enabled": "off", "time": "03:00", "random_range": 120})
        # The coordinator's live readings must not be mutated in passing.
        self.assertEqual(readings["auto_upgrade_config"]["enabled"], "on")

    def test_auto_upgrade_toggle_survives_a_hub_that_sent_no_schedule(self):
        self.assertEqual(status.auto_upgrade_config({}, True),
                         {"enabled": "on"})

    def test_face_detection_toggle_sends_the_tags_back(self):
        """A bare `enabled` is refused with -40211; only the whole block works."""
        readings = {"face_detection_tags": ["family", "courier"]}
        self.assertEqual(status.face_detection_config(readings, False),
                         {"enabled": "off", "tags": ["family", "courier"]})
        self.assertEqual(status.face_detection_config(readings, True)["enabled"],
                         "on")
        # The coordinator's live readings must not be mutated in passing.
        self.assertEqual(readings["face_detection_tags"], ["family", "courier"])

    def test_face_detection_toggle_survives_a_hub_that_listed_no_tags(self):
        self.assertEqual(status.face_detection_config({}, True),
                         {"enabled": "on", "tags": []})

    def test_face_detection_payload_wraps_the_block_the_hub_expects(self):
        client = self._client()
        client.set_face_detection({"enabled": "off", "tags": ["family"]})
        self.assertEqual(client._hub.calls[-1], ("setFaceDetectionConfig", {
            "face_detection": {"detection": {
                "enabled": "off", "tags": ["family"]}}}))

    def test_auto_upgrade_payload_wraps_the_block_the_hub_expects(self):
        client = self._client()
        client.set_auto_upgrade({"enabled": "off", "time": "03:00"})
        self.assertEqual(
            client._hub.calls[-1],
            ("setFirmwareAutoUpgradeConfig",
             {"auto_upgrade": {"common": {"enabled": "off", "time": "03:00"}}}))

    def test_readings_expose_the_new_settings(self):
        readings = status.hub_readings({
            "getDiagnoseMode": {"system": {"sys": {"diagnose_mode": "off"}}},
            "getFirmwareAutoUpgradeConfig": {"auto_upgrade": {"common": {
                "enabled": "on", "time": "03:00", "random_range": 120}}},
        })
        self.assertFalse(readings["diagnose_mode"])
        self.assertTrue(readings["auto_upgrade"])
        self.assertEqual(readings["auto_upgrade_time"], "03:00")
        self.assertEqual(readings["auto_upgrade_config"]["random_range"], 120)

    def test_missing_settings_do_not_break_the_poll(self):
        readings = status.hub_readings({})
        self.assertIsNone(readings["diagnose_mode"])
        self.assertIsNone(readings["auto_upgrade"])
        self.assertEqual(readings["auto_upgrade_config"], {})

    def test_every_batched_request_is_a_read(self):
        # The poll runs unattended every few seconds; a setter in here would
        # rewrite the user's hub on a timer.
        for name, _ in status.HUB_STATUS_REQUESTS:
            self.assertTrue(name.startswith("get"), name)


class LiveProbeTest(unittest.TestCase):
    """The live-view sweep in tools/, which needs no hub to reason about."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
        self.addCleanup(sys.path.pop, 0)
        import h500_session
        import probe_live
        self.session = importlib.reload(h500_session)
        self.probe = probe_live
        self.session.state.update(
            {"client": _StubLiveClient(), "raw": True, "requests": 0})

    def _run(self, outcomes, only=None):
        """Drive live_probe with a scripted verdict per attempt."""
        calls = []

        def fake_attempt(client, query, payload, timeout):
            calls.append((query, payload))
            return outcomes[len(calls) - 1]

        with patch.object(self.probe, "run_attempt", fake_attempt), \
                patch.object(self.session.time, "sleep", lambda _: None):
            return self.session.live_probe(0, 1.0, only, 0.0), calls

    def test_the_pytapo_combination_is_tried_first(self):
        result, calls = self._run([("closed", "")] * 20)
        first = result["results"][0]
        # Query type and payload block must differ; pairing them by name is
        # what made every earlier live run inconclusive.
        self.assertEqual(first["type"], "video")
        self.assertEqual(first["block"], "preview")
        self.assertNotEqual(first["type"], first["block"])
        self.assertNotIn("media_type", calls[0][0])
        self.assertIn("preview", calls[0][1]["params"])

    def test_it_stops_at_the_first_attempt_returning_video(self):
        result, calls = self._run([("closed", ""), ("video", 4096)])
        self.assertEqual(len(calls), 2, "should not keep probing after video")
        self.assertEqual(result["results"][-1]["found"],
                         "query type=video, block=preview, pytapo identity fields")

    def test_a_refusing_port_stops_the_sweep(self):
        result, calls = self._run(
            [("exception", "ConnectionRefusedError: refused")] * 5)
        self.assertEqual(len(calls), 1, "later attempts would be meaningless")
        self.assertIn("refusing", result["results"][-1]["stopped"])

    def test_only_selects_a_single_attempt(self):
        result, calls = self._run([("closed", "")], only="video-preview-pytapo")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["results"][0]["attempt"], "video-preview-pytapo")

    def test_an_unknown_label_is_reported_rather_than_silently_doing_nothing(self):
        result, calls = self._run([("closed", "")], only="nope")
        self.assertEqual(calls, [])
        self.assertIn("unknown attempt", result["error"])

    def test_pytapo_identity_omits_fields_the_hub_resolves_itself(self):
        block = self.probe.build_payload(
            "preview", {"device_id": "c", "mac": "m"}, "p", 1, "pytapo",
        )["params"]["preview"]
        self.assertEqual(block["deviceId"], "c")
        self.assertFalse({"dev_id", "mac", "client_id", "player_id"} & set(block))


class _StubMediaSession:
    """Replays a scripted sequence of media-session responses."""

    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def transceive(self, payload, no_data_timeout=None):
        async def stream():
            for mimetype, body in self._responses:
                yield types.SimpleNamespace(mimetype=mimetype, plaintext=body)
        return stream()


# The hub's real reply to an accepted live request, from a firmware 1.3.20 run.
OPEN_ACK = ("application/json",
            b'{"type":"response","seq":3704,'
            b'"params":{"error_code":0,"session_id":"10"}}')


class LiveAttemptTest(unittest.TestCase):
    """A successful open acknowledgement must not end the attempt.

    The hub answers an accepted live request with error_code 0 and a
    session_id, and only then sends video. Treating that reply as the verdict
    reported a working live session as a dead one.
    """

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
        self.addCleanup(sys.path.pop, 0)
        import probe_live
        self.probe = probe_live

    def _attempt(self, responses, timeout=0.5):
        stub = types.SimpleNamespace(
            H500MediaSession=lambda **kwargs: _StubMediaSession(responses))
        client = types.SimpleNamespace(
            host="h", cloud_password="c", _super_secret_key="k",
            _encryption_method="m", username="admin")
        with patch.object(self.probe, "load_api", lambda: stub):
            return asyncio.run(self.probe.attempt(client, {}, {}, timeout))

    def test_video_after_the_ack_is_reported_as_video(self):
        verdict, _ = self._attempt([OPEN_ACK, ("video/mp2t", b"\x47" * 188)])
        self.assertEqual(verdict, "video", "the ack must not end the attempt")

    def test_an_ack_with_no_video_is_distinguished_from_silence(self):
        verdict, detail = self._attempt([OPEN_ACK])
        self.assertEqual(verdict, "opened")
        self.assertEqual(detail["session"]["params"]["session_id"], "10")

    def test_silence_with_no_ack_stays_closed(self):
        verdict, _ = self._attempt([])
        self.assertEqual(verdict, "closed")

    def test_a_rejection_is_still_terminal(self):
        verdict, detail = self._attempt(
            [("application/json", b'{"params":{"error_code":-40106}}')])
        self.assertEqual(verdict, "error")
        self.assertEqual(detail["code"], -40106)

    def test_only_query_type_video_is_ever_sent(self):
        # type=preview returned 401 and left port 8800 refusing TCP.
        self.assertEqual({a[1] for a in self.probe.ATTEMPTS}, {"video"})


class _StubLiveClient:
    player_id = "player"
    _client_id = 1

    def camera_at(self, index):
        return {"device_id": "DEADBEEFCAFE", "mac": "AABBCCDDEEFF",
                "channel_id": 0}


if __name__ == "__main__":
    unittest.main()
