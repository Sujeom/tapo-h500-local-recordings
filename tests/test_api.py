import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

API_PATH = Path(__file__).parents[1] / "custom_components" / "tapo_h500" / "api.py"
SPEC = importlib.util.spec_from_file_location("h500_api", API_PATH)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)
H500MediaSession = api.H500MediaSession
H500Client = api.H500Client
IncompleteRecordingError = api.IncompleteRecordingError
build_download_payload = api.build_download_payload
safe_filename = api.safe_filename


class ApiTest(unittest.TestCase):
    def test_payload_matches_verified_h500_shape(self):
        payload = build_download_payload(
            {"device_id": "child", "mac": "AABB", "channel_id": 0},
            10, 20, "player", 1,
        )
        self.assertEqual(payload["params"]["download"], {
            "dev_id": "child", "mac": "AABB", "channels": [0],
            "client_id": 1, "end_time": "20", "media_type": 0,
            "start_time": "10", "player_id": "player",
        })

    def test_initial_post_forces_zero_content_length(self):
        session = object.__new__(H500MediaSession)
        with patch.object(
            H500MediaSession.__mro__[1], "_send_http_request", new=AsyncMock()
        ) as parent_send:
            asyncio.run(session._send_http_request(
                b"POST /stream HTTP/1.1", {b"Content-Length": b"-1"}))
        self.assertEqual(parent_send.call_args.args[1][b"Content-Length"], b"0")

    def test_filename_cannot_escape_media_directory(self):
        self.assertEqual(safe_filename("Side Doorbell", 10), "Side_Doorbell_10.ts")
        self.assertEqual(safe_filename("../../bad", 10), "bad_10.ts")

    def test_stock_session_uses_verified_ack_window(self):
        client = H500Client("host", "admin", "local", "cloud")
        client._super_secret_key = ""
        client._encryption_method = object()
        camera = {"device_id": "child", "mac": "AABB", "channel_id": 0}

        class FakeSession:
            kwargs = None
            def __init__(self, **kwargs):
                FakeSession.kwargs = kwargs
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return None
            async def transceive(self, *_, **__):
                if False:
                    yield None

        async def consume():
            with patch.object(api, "H500MediaSession", FakeSession):
                return [part async for part in client.iter_recording(camera, 10, 20)]

        with self.assertRaises(IncompleteRecordingError):
            asyncio.run(consume())
        self.assertEqual(FakeSession.kwargs["window_size"], 25)

    def test_finished_notification_completes_stream(self):
        client = H500Client("host", "admin", "local", "cloud")
        client._super_secret_key = ""
        client._encryption_method = object()
        camera = {"device_id": "child", "mac": "AABB", "channel_id": 0}

        class Response:
            mimetype = "application/json"
            plaintext = b'{"type":"notification","params":{"event_type":"stream_status","status":"finished"}}'

        class FakeSession:
            def __init__(self, **_): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return None
            async def transceive(self, *_, **__):
                yield Response()

        async def consume_finished():
            with patch.object(api, "H500MediaSession", FakeSession):
                return [part async for part in client.iter_recording(camera, 10, 20)]

        self.assertEqual(asyncio.run(consume_finished()), [])


if __name__ == "__main__":
    unittest.main()
