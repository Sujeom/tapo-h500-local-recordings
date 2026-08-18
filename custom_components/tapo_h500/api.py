"""Minimal local Tapo H500 recording client."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator


from pytapo import Tapo
from pytapo.media_stream import session as media_session
from pytapo.media_stream.crypto import AESHelper
from pytapo.media_stream.session import HttpMediaSession

from .clips import attach_detections, flatten_clips, start_of
from .status import (
    HUB_STATUS_REQUESTS, basic_info, firmware_upgrade, unpack_multiple,
)

_LOGGER = logging.getLogger(__name__)


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


def check_media_port(host: str, port: int = 8800, timeout: float = 5.0) -> str:
    """One unauthenticated exchange with the media port, classified.

    The first request of every media session is unauthenticated by design --
    the digest challenge is the REPLY to it -- so this costs one small TCP
    round trip: no login, no session, no lockout risk. Verified against the
    hub on 2026-08-17: healthy answers HTTP 401 with a digest challenge; the
    known wedge accepts the connection and closes it before a single byte.

    Returns "healthy" (any HTTP bytes came back), "wedged" (the zero-byte
    close), "silent" (open but mute past the timeout), or "unreachable".
    Blocking; callers run it in an executor.
    """
    import socket
    request = (
        "POST /stream HTTP/1.1\r\n"
        "Content-Type: multipart/mixed;boundary=healthcheck\r\n"
        "Connection: keep-alive\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    ).encode()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return "unreachable"
    # From here the hub accepted the connection, so a failure is the wedge
    # shape: it closes on the request instead of answering it. Depending on
    # timing that surfaces as an empty read or as a reset mid-exchange, and
    # both mean the same thing.
    try:
        with sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            data = sock.recv(1024)
    except (socket.timeout, TimeoutError):
        return "silent"
    except ConnectionError:
        return "wedged"
    return "healthy" if data else "wedged"


class H500Client:
    def __init__(self, host, username, password, cloud_password, debug=False):
        self.debug = debug
        self.host = host
        self.username = username
        self.password = password
        self.cloud_password = cloud_password
        self.player_id = str(uuid.uuid4())
        # What the hub says it is: model, firmware and hardware revision.
        # Filled by connect() and read by the device registry, so Home
        # Assistant's device page has a firmware version on it.
        self.info: dict = {}
        self._hub = None
        self._client_id = 1
        self._super_secret_key = ""
        self._encryption_method = None
        self._lock = asyncio.Lock()
        # How many media sessions this process has opened. The hub serves them
        # for hours after a reboot and then starts closing port 8800 before
        # sending a byte, which is before authentication and so identifies
        # nothing; a running count is what makes "it wedges after N" visible.
        self._sessions = 0
        # Hub control calls run in executor threads from both the coordinator
        # and service handlers; pytapo's session is not thread safe.
        self._hub_lock = threading.RLock()
        self._detection_supported = True
        # Whether the hub accepts both per-camera searches in one envelope.
        # Proven on 1.3.20; a refusal downgrades to single calls for good.
        self._batch_supported = True

    def connect(self):
        self._hub = Tapo(
            self.host, self.username, self.password, self.cloud_password,
            playerID=self.player_id, redactConfidentialInformation=True,
            printDebugInformation=self.debug,
        )
        # The record is nested two levels down. Reading device_model off the
        # outer dictionary always found nothing, so the default fired, the
        # model came back empty and the guard below never ran -- this
        # integration would have attached happily to a C200.
        self.info = basic_info(self._hub.basicInfo)
        model = str(self.info.get("device_model") or "").upper()
        # Prefix, not equality: TP-Link ships region and revision suffixes --
        # "H500(EU)", "H500 V2" -- and an exact match would refuse every one
        # of those at setup, permanently, with an error that reads like a
        # wrong address. The guard's job is only "not some other device".
        if model and not model.startswith("H500"):
            raise ValueError(f"Expected an H500, found {model}")
        try:
            self._client_id = self._hub.getUserID()
        except Exception:
            # H500 currently rejects get_user_id. The app defaults this
            # per-download integer to 1.
            self._client_id = 1
        self._super_secret_key = self._hub.superSecretKey
        self._encryption_method = self._hub.getEncryptionMethod()
        return self.info

    def firmware_update(self) -> dict:
        """Ask the hub to ask the cloud whether newer firmware exists.

        pytapo's own two-request batch, verified on this hub 2026-08-17
        (both sub-requests error_code 0). The hub does the phoning; callers
        keep the cadence to hours, the same restraint the app shows.
        """
        with self._hub_lock:
            reply = self._hub.isUpdateAvailable()
        return firmware_upgrade(unpack_multiple(reply))

    def reboot(self):
        """Restart the hub, immediately. Blocking; run in an executor.

        pytapo's standard immediate-reboot verb, the one it uses across
        Tapo devices -- and a different method entirely from setReboot,
        which stays excluded because its parameters cannot be told apart
        from editing the nightly schedule. Recordings survive a reboot;
        the hub does one to itself every night at its scheduled time.
        """
        with self._hub_lock:
            return self._hub.executeFunction(
                "rebootDevice", {"system": {"reboot": "null"}})

    def rotate_player_id(self) -> str:
        """A fresh identity for the next media session: the case-D experiment.

        The recurring wedge might be stale hub state keyed to the reused
        player_id. That cannot be tested on demand -- it needs a wedged hub
        -- so the coordinator calls this once when the sentinel sees the
        wedge, and the per-session log already records how the next session
        went: success without a reboot confirms case D from the field, the
        same pre-auth failure rules it out. The ids themselves stay out of
        the log.
        """
        self.player_id = str(uuid.uuid4())
        _LOGGER.debug(
            "Media port wedged; the next session will use a fresh player_id "
            "-- if it succeeds without a hub reboot, stale state was keyed "
            "to the old id (case D)")
        return self.player_id

    def check_media(self) -> str:
        """check_media_port against this hub. Blocking; run in an executor."""
        return check_media_port(self.host)

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
        # Search the range that was asked for, as one epoch window.
        #
        # This used to ask searchDateWithVideo which dates held video and then
        # search each one. The hub answers with dates in its OWN local time and
        # ignores the range it was given -- asked for 20260813 it returns
        # 20260811 and 20260812 -- and those were read back as UTC dates. On a
        # hub at UTC-4 that silently dropped every clip between 8pm and
        # midnight local, because those sit on the next UTC date, which the hub
        # never names. searchVideoWithUTC takes plain epoch seconds and spans
        # days happily, so the date lookup bought a round trip and a bug.
        first = datetime.strptime(start_date, "%Y%m%d").replace(
            tzinfo=timezone.utc)
        last = datetime.strptime(end_date, "%Y%m%d").replace(
            tzinfo=timezone.utc) + timedelta(days=1)
        found = self._search_videos(
            camera, first.timestamp(), last.timestamp() - 1)
        for clip in found:
            moment = start_of(clip)
            if moment is not None:
                clip["date"] = datetime.fromtimestamp(
                    moment, timezone.utc).strftime("%Y%m%d")
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

    def activity(self, camera, start_time, end_time):
        """Both per-camera searches -- clips and detections -- in one round
        trip.

        Proven on firmware 1.3.20: a multipleRequest carrying both answers
        with results identical to the individual calls. Half the requests
        per poll, against a hub that is easy to overload. If the envelope is
        ever refused, this falls back to the two single calls and remembers.

        Returns (clips, detections) with exactly the semantics of recent()
        and detections(): a failed clip search raises, an unsupported
        detection search returns None and disables itself, a quiet window is
        empty answers.
        """
        if not self._batch_supported:
            return (self._search_videos(camera, start_time, end_time),
                    self.detections(camera, start_time, end_time))
        window = {
            "channel": 0, "child_device_id": camera["device_id"],
            "child_device_mac": camera["mac"],
            "start_time": int(start_time), "end_time": int(end_time),
            "start_index": 0, "end_index": 999,
        }
        requests = [
            {"method": "searchVideoWithUTC",
             "params": {"playback": {"search_video_with_utc": {
                 **window, "player_id": self.player_id}}}},
        ]
        if self._detection_supported:
            requests.append(
                {"method": "searchDetectionList",
                 "params": {"playback": {"search_detection_list": window}}})
        try:
            with self._hub_lock:
                reply = self._hub.performRequest({
                    "method": "multipleRequest",
                    "params": {"requests": requests},
                })
        except Exception:
            # The envelope itself was refused; degrade to the proven single
            # calls, this time and every later time.
            self._batch_supported = False
            return (self._search_videos(camera, start_time, end_time),
                    self.detections(camera, start_time, end_time))
        responses = (reply.get("result") or {}).get("responses") or []
        by_method = {item.get("method"): item for item in responses}
        videos = by_method.get("searchVideoWithUTC") or {}
        if videos.get("error_code", 0):
            raise RuntimeError(
                f"searchVideoWithUTC failed ({videos.get('error_code')})")
        clips = flatten_clips(videos.get("result") or {})
        if not self._detection_supported:
            return clips, None
        detection_reply = by_method.get("searchDetectionList") or {}
        if detection_reply.get("error_code", 0):
            self._detection_supported = False
            return clips, None
        detections = ((detection_reply.get("result") or {})
                      .get("playback", {}).get("search_detection_list"))
        # A quiet window is {} rather than an empty list: an answer, not a
        # refusal, exactly as in detections().
        return clips, detections if isinstance(detections, list) else []

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

    def set_face_detection(self, detection: dict):
        """Replace the whole detection block.

        The hub rejects a bare `enabled` with -40211; only the complete block,
        tags included, is accepted.
        """
        return self._set("setFaceDetectionConfig",
                         {"face_detection": {"detection": detection}})

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

    async def iter_recording(self, camera, start_time, end_time,
                             kind: str = "download") -> AsyncIterator[bytes]:
        """Stream one recording off the hub's media port.

        Every call is a whole session of its own: TCP connect, digest
        challenge, AES key exchange. One at a time, and each one ends with a
        debug record of how it went -- see `_sessions`. `kind` only labels
        that record, so previews and downloads can be told apart in a log.
        """
        queued = time.monotonic()
        async with self._lock:
            self._sessions += 1
            sequence, opened = self._sessions, time.monotonic()
            received, finished, ended = 0, False, "closed"
            try:
                payload = build_download_payload(
                    camera, start_time, end_time,
                    self.player_id, self._client_id)
                session = H500MediaSession(
                    ip=self.host, cloud_password=self.cloud_password,
                    super_secret_key=self._super_secret_key,
                    encryptionMethod=self._encryption_method, port=8800,
                    # Stock pytapo acknowledges every window. A 25-packet
                    # window reproduces the acknowledgement cadence verified
                    # on H500.
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
                    while True:
                        try:
                            response = await asyncio.wait_for(
                                stream.__anext__(), 35)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError as err:
                            raise IncompleteRecordingError(
                                "H500 recording stream stalled") from err
                        base_type = response.mimetype.split(";", 1)[0].strip()
                        if base_type == "application/json":
                            try:
                                message = json.loads(
                                    response.plaintext.decode())
                            except (UnicodeDecodeError, json.JSONDecodeError):
                                continue
                            params = message.get("params", {})
                            if message.get("type") == "response" \
                                    and params.get("error_code", 0):
                                raise IncompleteRecordingError(
                                    "H500 rejected recording request "
                                    f"({params['error_code']})")
                            if (
                                message.get("type") == "notification"
                                and params.get("event_type") == "stream_status"
                                and params.get("status") == "finished"
                            ):
                                finished = True
                                break
                        elif base_type == "video/mp2t":
                            received += len(response.plaintext)
                            yield response.plaintext
                    if not finished:
                        raise IncompleteRecordingError(
                            "H500 closed the stream without a finished "
                            "notification")
            except BaseException as err:
                # BaseException, not Exception: a consumer that stops reading
                # abandons this generator and the event loop finalises it
                # later with GeneratorExit, or CancelledError if it is tearing
                # down by then. Those are the cases worth seeing, and they are
                # exactly the ones an `except Exception` lets past unnamed.
                ended = type(err).__name__
                raise
            finally:
                # No host, camera, player id or clip time: this runs on every
                # preview, unattended, at whatever level the user has on.
                _LOGGER.debug(
                    "H500 media session %s (%s): %.2fs waiting, %s bytes, "
                    "finished=%s, %s after %.2fs",
                    sequence, kind, opened - queued, received, finished,
                    ended, time.monotonic() - opened)
