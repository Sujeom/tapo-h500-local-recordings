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


if __name__ == "__main__":
    unittest.main()
