"""Minimal local Tapo H500 recording client."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator


from pytapo import Tapo
from pytapo.media_stream import session as media_session
from pytapo.media_stream.crypto import AESHelper
from pytapo.media_stream.session import HttpMediaSession

from .clips import attach_detections, flatten_clips, start_of
from .status import HUB_STATUS_REQUESTS, unpack_multiple


class _EmptyNonce(bytes):
    """Empty, but truthy.

    The H500 reports media encryption as on and then sends Key-Exchange with
    nonce="". pytapo rejects any falsy nonce outright, yet an empty one is not
    fatal: the key is md5(nonce + b":" + hashed_password) and the IV is
    md5(username + b":" + nonce), both of which are defined for b"". Carrying
    the emptiness through as a truthy value keeps pytapo's key derivation
    intact rather than reimplementing it here.
    """

    def __bool__(self):
        return True


class H500AESHelper(AESHelper):
    def __init__(self, username, nonce, cloud_password, super_secret_key,
                 encryptionMethod):
        super().__init__(username, nonce or _EmptyNonce(), cloud_password,
                         super_secret_key, encryptionMethod)


# The session module resolves AESHelper by name, so replacing it there is what
# makes the subclass take effect.
media_session.AESHelper = H500AESHelper


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


class H500Client:
    def __init__(self, host, username, password, cloud_password, debug=False):
        self.debug = debug
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
        # Hub control calls run in executor threads from both the coordinator
        # and service handlers; pytapo's session is not thread safe.
        self._hub_lock = threading.RLock()
        self._detection_supported = True

    def connect(self):
        self._hub = Tapo(
            self.host, self.username, self.password, self.cloud_password,
            playerID=self.player_id, redactConfidentialInformation=True,
            printDebugInformation=self.debug,
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
        with self._hub_lock:
            result = self._hub.executeFunction(
                "getGeneralDeviceList",
                {"general_camera_manage": {"paired_general_device_list": {}}},
            )
        return result.get("general_camera_manage", {}).get(
            "paired_general_device_list", [])

    def camera_at(self, index):
        cameras = self.cameras()
        if index < 0 or index >= len(cameras):
            raise ValueError(
                f"Camera index must be between 0 and {len(cameras) - 1}")
        return cameras[index]

    def _search_videos(self, camera, start_time, end_time):
        """One indexed-clip lookup over an exact UTC window."""
        with self._hub_lock:
            clips = self._hub.executeFunction(
                "searchVideoWithUTC",
                {"playback": {"search_video_with_utc": {
                    "channel": 0, "child_device_id": camera["device_id"],
                    "child_device_mac": camera["mac"],
                    "start_time": int(start_time),
                    "end_time": int(end_time),
                    "start_index": 0, "end_index": 999,
                    "player_id": self.player_id,
                }}},
            )
        return flatten_clips(clips)

    def recordings(self, camera_index=0, start_date=None, end_date=None):
        camera = self.camera_at(camera_index)
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
        with self._hub_lock:
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
                for clip in self._search_videos(
                        camera, day.timestamp(), day.timestamp() + 86399):
                    clip["date"] = value["date"]
                    found.append(clip)
        # One detection lookup over the whole range rather than one per day:
        # a seven-day window answers fine, and this hub is easy to overload.
        if found:
            moments = [start_of(clip) for clip in found]
            moments = [moment for moment in moments if moment is not None]
            if moments:
                attach_detections(found, self.detections(
                    camera, min(moments) - 60, max(moments) + 60))
        return camera, found

    def recent(self, camera, start_time, end_time):
        """Clips indexed in a short window, used by the event poller."""
        return self._search_videos(camera, start_time, end_time)

    def detections(self, camera, start_time, end_time):
        """Hub-side detection log: what actually triggered each recording.

        Verified on firmware 1.3.20 -- every clip in a three-day window had a
        detection whose start_time matched it exactly, carrying an alarm_type
        and an events_1 bitmask.

        An empty reply means "nothing in this window", which is the normal
        state of a quiet camera. Treating it as "unsupported" is what made this
        look dead: one quiet poll disabled the call for the rest of the session
        and it was never retried, so the classification never arrived.
        """
        if not self._detection_supported:
            return None
        try:
            with self._hub_lock:
                result = self._hub.executeFunction(
                    "searchDetectionList",
                    {"playback": {"search_detection_list": {
                        "channel": 0, "child_device_id": camera["device_id"],
                        "child_device_mac": camera["mac"],
                        "start_time": int(start_time),
                        "end_time": int(end_time),
                        "start_index": 0, "end_index": 999,
                    }}},
                )
        except Exception:
            self._detection_supported = False
            return None
        detections = result.get("playback", {}).get("search_detection_list")
        # A quiet window is {} rather than an empty list. That is an answer,
        # not a refusal, so it must not disable the call.
        return detections if isinstance(detections, list) else []

    def hub_status(self):
        """Every hub-level reading in a single round trip.

        Batched deliberately: this hub is easy to wedge, and one
        multipleRequest costs the same as one getter.
        """
        with self._hub_lock:
            response = self._hub.performRequest({
                "method": "multipleRequest",
                "params": {"requests": [
                    {"method": name, "params": params}
                    for name, params in HUB_STATUS_REQUESTS]},
            })
        return unpack_multiple(response)

    def _set(self, method, params):
        with self._hub_lock:
            return self._hub.executeFunction(method, params)

    def set_led(self, on: bool):
        return self._set("setLedStatus",
                         {"led": {"config": {"enabled": "on" if on else "off"}}})

    def set_loop_recording(self, on: bool):
        return self._set(
            "setCircularRecordingConfig",
            {"harddisk_manage": {"harddisk": {"loop": "on" if on else "off"}}})

    def set_diagnose_mode(self, on: bool):
        return self._set(
            "setDiagnoseMode",
            {"system": {"sys": {"diagnose_mode": "on" if on else "off"}}})

    def set_auto_upgrade(self, config: dict):
        """Replace the whole auto-upgrade block.

        The hub takes `common` wholesale, so the caller passes the current
        block with only the field it means to change replaced; sending just
        `enabled` would drop the schedule.
        """
        return self._set("setFirmwareAutoUpgradeConfig",
                         {"auto_upgrade": {"common": config}})

    def siren_tones(self):
        """The hub's own list of siren sounds.

        Fetched once at setup rather than every poll: it is a fixed table.
        """
        with self._hub_lock:
            result = self._hub.executeFunction("getSirenTypeList", {"siren": {}})
        tones = result.get("siren_type_list")
        return [tone for tone in tones if isinstance(tone, str)] \
            if isinstance(tones, list) else []

    def set_siren(self, on: bool):
        return self._set("setSirenStatus",
                         {"siren": {"status": "on" if on else "off"}})

    def set_siren_config(self, tone=None, volume=None, duration=None):
        """Change the sound, loudness or run time of the hub siren.

        Volume is 1-10; the hub rejects 0 and 11 with -40209. Sending only the
        fields that changed is what the app does and what the hub expects.
        """
        siren = {}
        if tone is not None:
            siren["siren_type"] = tone
        if volume is not None:
            siren["volume"] = str(volume)
        if duration is not None:
            siren["duration"] = int(duration)
        if not siren:
            return None
        return self._set("setSirenConfig", {"siren": siren})

    def format_storage(self):
        """Erase hub storage.

        The hub exposes no per-clip delete, so this is the only hub-side
        deletion that exists. It destroys every recording on the hub.
        """
        with self._hub_lock:
            return self._hub.executeFunction(
                "formatSdCard", {"harddisk_manage": {"format_hd": "1"}})

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
