"""Minimal local Tapo H500 recording client."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from pytapo import Tapo
from pytapo.media_stream.session import HttpMediaSession


class IncompleteRecordingError(Exception):
    """The hub did not confirm that the recording stream completed."""


class H500MediaSession(HttpMediaSession):
    """Match the H500's required outer POST framing."""

    async def _send_http_request(self, delimiter, headers):
        headers = dict(headers)
        if delimiter.startswith(b"POST "):
            headers[b"Content-Length"] = b"0"
        await super()._send_http_request(delimiter, headers)


def build_download_payload(camera, start_time, end_time, player_id, client_id):
    return {
        "type": "request",
        "seq": 1,
        "params": {"method": "get", "download": {
            "dev_id": camera["device_id"],
            "mac": camera["mac"],
            "channels": [int(camera.get("channel_id", 0))],
            "client_id": client_id,
            "end_time": str(end_time),
            "media_type": 0,
            "start_time": str(start_time),
            "player_id": player_id,
        }},
    }


def safe_filename(alias, start_time):
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", alias).strip("_.") or "camera"
    name = name[:80]
    return f"{name}_{start_time}.ts"


class H500Client:
    def __init__(self, host, username, password, cloud_password):
        self.host = host
        self.username = username
        self.password = password
        self.cloud_password = cloud_password
        self.player_id = str(uuid.uuid4())
        self._hub = None
        self._client_id = 1
        self._super_secret_key = ""
        self._encryption_method = None
        self._lock = asyncio.Lock()

    def connect(self):
        self._hub = Tapo(
            self.host, self.username, self.password, self.cloud_password,
            playerID=self.player_id, redactConfidentialInformation=True,
        )
        info = self._hub.basicInfo
        model = str(info.get("device_model", info.get("model", ""))).upper()
        if model and model != "H500":
            raise ValueError(f"Expected an H500, found {model}")
        try:
            self._client_id = self._hub.getUserID()
        except Exception:
            # H500 currently rejects get_user_id. The app defaults this
            # per-download integer to 1.
            self._client_id = 1
        self._super_secret_key = self._hub.superSecretKey
        self._encryption_method = self._hub.getEncryptionMethod()
        return info

    def close(self):
        if self._hub:
            self._hub.close()
            self._hub = None

    def cameras(self):
        result = self._hub.executeFunction(
            "getGeneralDeviceList",
            {"general_camera_manage": {"paired_general_device_list": {}}},
        )
        return result.get("general_camera_manage", {}).get(
            "paired_general_device_list", [])

    def recordings(self, camera_index=0, start_date=None, end_date=None):
        cameras = self.cameras()
        if camera_index < 0 or camera_index >= len(cameras):
            raise ValueError(f"Camera index must be between 0 and {len(cameras) - 1}")
        camera = cameras[camera_index]
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        start_date = start_date or today
        end_date = end_date or today
        for label, value in (("start_date", start_date), ("end_date", end_date)):
            try:
                datetime.strptime(value, "%Y%m%d")
            except (TypeError, ValueError) as err:
                raise ValueError(f"{label} must use YYYYMMDD") from err
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        dates = self._hub.executeFunction(
            "searchDateWithVideo",
            {"playback": {"search_year_utility": {
                "channel": [0], "child_device_id": camera["device_id"],
                "child_device_mac": camera["mac"], "start_date": start_date,
                "end_date": end_date,
            }}},
        )
        found = []
        for result in dates.get("playback", {}).get("search_results", []):
            for value in result.values():
                if "date" not in value:
                    continue
                day = datetime.strptime(value["date"], "%Y%m%d").replace(
                    tzinfo=timezone.utc)
                clips = self._hub.executeFunction(
                    "searchVideoWithUTC",
                    {"playback": {"search_video_with_utc": {
                        "channel": 0, "child_device_id": camera["device_id"],
                        "child_device_mac": camera["mac"],
                        "start_time": int(day.timestamp()),
                        "end_time": int(day.timestamp()) + 86399,
                        "start_index": 0, "end_index": 999,
                        "player_id": self.player_id,
                    }}},
                )
                for group in clips.get("playback", {}).get(
                        "search_video_results", []):
                    for clip in group.values():
                        clip["date"] = value["date"]
                        found.append(clip)
        return camera, found

    async def iter_recording(self, camera, start_time, end_time) -> AsyncIterator[bytes]:
        async with self._lock:
            payload = build_download_payload(
                camera, start_time, end_time, self.player_id, self._client_id)
            session = H500MediaSession(
                ip=self.host, cloud_password=self.cloud_password,
                super_secret_key=self._super_secret_key,
                encryptionMethod=self._encryption_method, port=8800,
                # Stock pytapo acknowledges every window. A 25-packet window
                # reproduces the acknowledgement cadence verified on H500.
                username=self.username, window_size=25,
                query_params={
                    "deviceId": camera["device_id"], "type": "download",
                    "playerId": self.player_id, "media_type": 0,
                },
            )
            async with session:
                stream = session.transceive(
                    json.dumps(payload, separators=(",", ":")),
                    no_data_timeout=30,
                )
                finished = False
                while True:
                    try:
                        response = await asyncio.wait_for(stream.__anext__(), 35)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as err:
                        raise IncompleteRecordingError(
                            "H500 recording stream stalled") from err
                    base_type = response.mimetype.split(";", 1)[0].strip()
                    if base_type == "application/json":
                        try:
                            message = json.loads(response.plaintext.decode())
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        params = message.get("params", {})
                        if message.get("type") == "response" and params.get("error_code", 0):
                            raise IncompleteRecordingError(
                                f"H500 rejected recording request ({params['error_code']})")
                        if (
                            message.get("type") == "notification"
                            and params.get("event_type") == "stream_status"
                            and params.get("status") == "finished"
                        ):
                            finished = True
                            break
                    elif base_type == "video/mp2t":
                        yield response.plaintext
                if not finished:
                    raise IncompleteRecordingError(
                        "H500 closed the stream without a finished notification")
