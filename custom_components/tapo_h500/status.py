"""Reading the hub's status responses.

Pure functions over what the hub returns, so they can be tested without a hub
or a Home Assistant install. Every key here came from an observed response —
see docs/protocol-notes.md.
"""
from __future__ import annotations

import re
import time

from .const import SIREN_VOLUME_MAX, SIREN_VOLUME_MIN

# One round trip covers all of these. Each was confirmed to return data on an
# H500 running firmware 1.3.20; the other 55 getters pytapo knows about do not.
HUB_STATUS_REQUESTS = (
    ("getSdCardStatus", {"harddisk_manage": {"table": ["hd_info"]}}),
    ("getSirenStatus", {"siren": {}}),
    ("getSirenConfig", {"siren": {}}),
    ("getFirmwareUpdateStatus", {"cloud_config": {"name": "upgrade_status"}}),
    ("getLedStatus", {"led": {"name": ["config"]}}),
    ("getCircularRecordingConfig", {"harddisk_manage": {"name": "harddisk"}}),
    ("getMediaEncrypt", {"cet": {"name": ["media_encrypt"]}}),
    ("getDeviceIpAddress", {"network": {"name": ["wan"]}}),
    ("getDiagnoseMode", {"system": {"name": "sys"}}),
    ("getFaceDetectionConfig", {"face_detection": {"name": "config"}}),
    ("getFirmwareAutoUpgradeConfig", {"auto_upgrade": {"name": ["common"]}}),
    ("getClockStatus", {"system": {"name": "clock_status"}}),
    ("getTimezone", {"system": {"name": ["basic"]}}),
    ("getUsrDefAudioList", {"usr_def_audio": {"name": "config"}}),
)

SIZE = re.compile(r"([0-9.]+)\s*([KMGT]?B)", re.IGNORECASE)
UNITS = {"B": 1 / 1024**3, "KB": 1 / 1024**2, "MB": 1 / 1024, "GB": 1, "TB": 1024}


def unpack_multiple(response) -> dict:
    """Successful sub-responses of a multipleRequest, keyed by method."""
    if not isinstance(response, dict):
        return {}
    found = {}
    for item in response.get("result", {}).get("responses", []):
        if isinstance(item, dict) and item.get("error_code") == 0 and item.get("result"):
            found[item.get("method")] = item["result"]
    return found


def dig(data, *path):
    """Follow a key path, returning None rather than raising."""
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def gigabytes(value) -> float | None:
    """The hub reports sizes as strings like "7.62 GB"."""
    match = SIZE.match(str(value or "").strip())
    if not match:
        return None
    return round(float(match.group(1)) * UNITS[match.group(2).upper()], 2)


def disk(status: dict) -> dict:
    """The first disk's record, which is nested one level deeper than it looks.

    The shape is {"hd_info": [{"hd_info_1": {...}}]}, so the useful dictionary
    is the sole value of the sole list entry.
    """
    entries = dig(status.get("getSdCardStatus"), "harddisk_manage", "hd_info")
    if not isinstance(entries, list) or not entries:
        return {}
    first = entries[0]
    if not isinstance(first, dict) or not first:
        return {}
    inner = next(iter(first.values()))
    return inner if isinstance(inner, dict) else {}


def hub_volume(level: float) -> int:
    """Home Assistant's 0.0-1.0 siren level onto the hub's 1-10.

    Clamped rather than trusted: the hub rejects 0 and 11 with -40209, and 0.0
    is a level Home Assistant will legitimately send.
    """
    return max(SIREN_VOLUME_MIN,
               min(SIREN_VOLUME_MAX, round(level * SIREN_VOLUME_MAX)))


def clock_offset(hub_epoch, now: float | None = None) -> int | None:
    """Seconds the hub's clock is ahead of ours, or None if it did not say.

    Signed on purpose: ahead and behind are different problems, and rounding
    them together would hide a hub drifting one way.
    """
    seconds = _int(hub_epoch)
    if seconds is None:
        return None
    return int(round(seconds - (time.time() if now is None else now)))


def used_audio_slots(status: dict) -> list[str]:
    """Named custom-sound slots the hub holds.

    getUsrDefAudioList always returns all five slots; the empty ones carry
    empty strings rather than being absent, so presence proves nothing and the
    name is what has to be checked.
    """
    files = dig(status.get("getUsrDefAudioList"), "usr_def_audio")
    if not isinstance(files, dict):
        # This runs inside the poll; a shape nobody anticipated must not take
        # every other reading down with it.
        return []
    names = []
    for key, slot in sorted(files.items()):
        if not key.startswith("file_") or not isinstance(slot, dict):
            continue
        name = str(slot.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def face_detection_config(readings: dict, on: bool) -> dict:
    """The whole detection block with only `enabled` changed.

    setFaceDetectionConfig refuses `enabled` on its own with -40211 and accepts
    only the complete block, so the tag list has to go back with every toggle.
    Same trap as the auto-upgrade schedule: send half the block and the hub
    either rejects it or keeps what it was not told about.
    """
    return {
        "enabled": "on" if on else "off",
        "tags": list(readings.get("face_detection_tags") or []),
    }


def auto_upgrade_config(readings: dict, on: bool) -> dict:
    """The whole auto-upgrade block with only `enabled` changed.

    setFirmwareAutoUpgradeConfig replaces `common` wholesale, so a toggle has
    to send back the time and window it is not changing. Sending just
    `enabled` would silently wipe the schedule. Copied rather than mutated:
    this is the coordinator's live readings dict.
    """
    config = dict(readings.get("auto_upgrade_config") or {})
    config["enabled"] = "on" if on else "off"
    return config


def _int(value) -> int | None:
    """The hub sends numbers as strings about half the time."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _on(value) -> bool | None:
    if value is None:
        return None
    return str(value).lower() in ("on", "1", "true", "enabled")


def hub_readings(status: dict, now: float | None = None) -> dict:
    """Flatten the hub's status into the values entities are built from.

    `now` exists so the clock comparison is deterministic under test.
    """
    hd = disk(status)
    free = gigabytes(hd.get("video_free_space"))
    total = gigabytes(hd.get("video_total_space"))
    used_percent = None
    if free is not None and total:
        used_percent = round((total - free) / total * 100, 1)
    siren = status.get("getSirenStatus") or {}
    siren_config = status.get("getSirenConfig") or {}
    upgrade = dig(status.get("getFirmwareAutoUpgradeConfig"),
                  "auto_upgrade", "common") or {}
    face = dig(status.get("getFaceDetectionConfig"),
               "face_detection", "detection") or {}
    clock = dig(status.get("getClockStatus"), "system", "clock_status") or {}
    basic = dig(status.get("getTimezone"), "system", "basic") or {}
    return {
        "siren_tone": siren_config.get("siren_type"),
        # 1-10 as a string on the wire; kept numeric for the volume slider.
        "siren_volume": _int(siren_config.get("volume")),
        "siren_duration": _int(siren_config.get("duration")),
        "storage_free_gb": free,
        "storage_total_gb": total,
        "storage_used_percent": used_percent,
        "storage_status": hd.get("status"),
        "storage_healthy": None if hd.get("status") is None
        else hd.get("status") == "normal",
        "siren_active": None if siren.get("status") is None
        else str(siren.get("status")).lower() != "off",
        "siren_time_left": siren.get("time_left"),
        "firmware_state": dig(status.get("getFirmwareUpdateStatus"),
                              "cloud_config", "upgrade_status", "state"),
        "led_on": _on(dig(status.get("getLedStatus"), "led", "config", "enabled")),
        "loop_recording": _on(dig(status.get("getCircularRecordingConfig"),
                                  "harddisk_manage", "harddisk", "loop")),
        "media_encrypted": _on(dig(status.get("getMediaEncrypt"), "cet",
                                   "media_encrypt", "enabled")),
        "ip_address": dig(status.get("getDeviceIpAddress"), "network", "wan",
                          "ipaddr"),
        "diagnose_mode": _on(dig(status.get("getDiagnoseMode"), "system", "sys",
                                 "diagnose_mode")),
        "auto_upgrade": _on(upgrade.get("enabled")),
        # Kept whole: setFirmwareAutoUpgradeConfig replaces the entire block, so
        # toggling it has to send back the time and window it did not change.
        "auto_upgrade_config": upgrade,
        "auto_upgrade_time": upgrade.get("time"),
        # Read-only: getFaceDetectionConfig answers, but setFaceDetectionConfig
        # refuses even a write of the hub's own current value (-40211).
        "face_detection": _on(face.get("enabled")),
        "face_detection_tags": face.get("tags") or [],
        "hub_clock": clock.get("seconds_from_1970"),
        "hub_local_time": clock.get("local_time"),
        # Signed drift between the hub and Home Assistant. Not cosmetic: clip
        # filenames and the media browser's date folders are derived from these
        # timestamps, so a hub whose clock wanders files recordings under the
        # wrong day.
        "clock_offset": clock_offset(clock.get("seconds_from_1970"), now),
        "timezone": basic.get("zone_id") or basic.get("timezone"),
        "custom_sounds": len(used_audio_slots(status)),
        "custom_sound_names": used_audio_slots(status),
    }
