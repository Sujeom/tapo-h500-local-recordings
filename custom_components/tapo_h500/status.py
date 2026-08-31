"""Reading the hub's status responses.

Pure functions over what the hub returns, so they can be tested without a hub
or a Home Assistant install. Every key here came from an observed response —
see docs/protocol-notes.md.
"""
from __future__ import annotations

from typing import Any

import re
import time

from .const import (
    EMPTIED_PERCENT, MIN_TREND_SECONDS, SIREN_VOLUME_MAX, SIREN_VOLUME_MIN,
)

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
    # Read only, and deliberately. The hub can reboot itself on a schedule,
    # and a hub that does that is a hub with a gap in its recordings -- which
    # looks exactly like a camera that stopped working.
    #
    # `setReboot` is not called from anywhere and will not be: its params
    # (`timing_reboot`) are ambiguous between scheduling a reboot and
    # performing one, and a wrong guess reboots the hub mid-download.
    ("getReboot", {"timing_reboot": {"name": ["reboot"]}}),
)

SIZE = re.compile(r"([0-9.]+)\s*([KMGT]?B)", re.IGNORECASE)
UNITS = {"B": 1 / 1024**3, "KB": 1 / 1024**2, "MB": 1 / 1024, "GB": 1, "TB": 1024}


def unpack_multiple(response: Any) -> dict[str, Any]:
    """Successful sub-responses of a multipleRequest, keyed by method."""
    if not isinstance(response, dict):
        return {}
    found = {}
    for item in response.get("result", {}).get("responses", []):
        if isinstance(item, dict) and item.get("error_code") == 0 and item.get("result"):
            found[item.get("method")] = item["result"]
    return found


def dig(data: Any, *path: str) -> Any:
    """Follow a key path, returning None rather than raising."""
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def gigabytes(value: Any) -> float | None:
    """The hub reports sizes as strings like "7.62 GB"."""
    match = SIZE.match(str(value or "").strip())
    if not match:
        return None
    return round(float(match.group(1)) * UNITS[match.group(2).upper()], 2)


def basic_info(response: Any) -> dict[str, Any]:
    """The device record inside whatever shape pytapo's basicInfo returned.

    `getDeviceInfo` answers `{"device_info": {"basic_info": {...}}}`, and
    reading `device_model` off the outer dictionary finds nothing at all --
    which is exactly what the H500 model check did, so the guard that was meant
    to stop this integration attaching to a C200 has never once run.

    Two shapes because pytapo has two: its KLAP branch returns the record flat.
    Its own code tests for both, and so does this.
    """
    nested = dig(response, "device_info", "basic_info")
    if isinstance(nested, dict):
        return nested
    return response if isinstance(response, dict) else {}


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


def clock_offset(hub_epoch: Any, now: float | None = None) -> int | None:
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


def reboot_schedule(block: dict) -> str | None:
    """The hub's own scheduled reboot, as one readable state.

    None when the hub said nothing at all, which is different from "off" and
    has to stay different: this is a getter whose params came from pytapo
    rather than from a live probe, so an unanswered call must read as unknown
    rather than as a hub that never reboots itself.

    The time is reported only while the schedule is on. It is stored either
    way, and showing "03:00:00" for a hub that is not going to reboot would be
    the more alarming of the two wrong answers.
    """
    if not block:
        return None
    if not _on(block.get("enabled")):
        return "off"
    return str(block.get("time") or "").strip() or "on"


def _int(value: Any) -> int | None:
    """The hub sends numbers as strings about half the time."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _on(value: Any) -> bool | None:
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
    reboot = dig(status.get("getReboot"), "timing_reboot", "reboot") or {}
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
        # A hub that reboots itself on a schedule has a gap in its recordings
        # at that hour, which looks exactly like a camera that stopped
        # working. Read only; nothing here writes it.
        "scheduled_reboot": reboot_schedule(reboot),
        "scheduled_reboot_enabled": _on(reboot.get("enabled")),
        # The hub's own numbering, unverified -- 0 was seen on a schedule that
        # was switched off, which says nothing about which day it means. Passed
        # through as it arrived rather than translated into a weekday.
        "scheduled_reboot_day": reboot.get("day"),
    }


def fill_rate(samples: list[tuple[int, float]]) -> float | None:
    """Percent of the hub's disk filled per hour, by least squares.

    `samples` is (unix seconds, percent used), oldest first.

    None until there is enough to say anything. The hub rounds the figure to a
    tenth of a percent, so on a 512 GB card one step is half a gigabyte: two
    readings a minute apart measure that rounding, not a trend. A least-squares
    fit over the whole run rather than first-versus-last, because the rounding
    makes the endpoints the two least reliable points to build a line from.
    """
    if not samples:
        return None
    # One guard, deliberately. Earlier versions also checked for fewer than
    # two samples and for a zero spread in the timestamps, and neither could
    # ever fire: samples are appended in time order, so an hour of span means
    # at least two distinct instants, which means a non-zero spread. Removing
    # either changed no behaviour, so no test could tell a broken one from a
    # working one. The span is the only condition, and it lives here.
    if samples[-1][0] - samples[0][0] < MIN_TREND_SECONDS:
        return None
    count = len(samples)
    mean_at = sum(at for at, _ in samples) / count
    mean_used = sum(used for _, used in samples) / count
    spread = sum((at - mean_at) ** 2 for at, _ in samples)
    slope = sum((at - mean_at) * (used - mean_used)
                for at, used in samples) / spread
    return slope * 3600


def hours_until_full(samples: list[tuple[int, float]],
                     used: float | None) -> float | None:
    """How long until the hub starts overwriting, at the current rate.

    "Full" is not a failure here -- loop recording does not stop at 100%, it
    silently discards the oldest footage -- so this is the deadline for
    downloading anything worth keeping.

    None whenever the answer is not known: too little history, or a disk that
    is not filling. A hub whose oldest footage is already being overwritten
    sits at a steady figure forever, and reporting "full in 4000 days" from
    the noise in that would be worse than saying nothing.
    """
    rate = fill_rate(samples)
    if rate is None or rate <= 0 or used is None:
        return None
    # No separate case for an already-full disk: the figure is computed as
    # (total - free) / total, so it cannot exceed 100, and at exactly 100 this
    # is already zero.
    return (100 - used) / rate


def trend_samples(previous: list[tuple[int, float]], at: int,
                  used: float | None, cap: int) -> list[tuple[int, float]]:
    """Add one reading to the history, dropping it when the disk was emptied.

    A format, a swapped card or a hub that starts overwriting all show up the
    same way: the figure falls. Keeping the readings from before that point
    would fit a line across the drop and forecast from a slope that never
    happened, so the history starts again.
    """
    if used is None:
        return previous
    if previous and used < previous[-1][1] - EMPTIED_PERCENT:
        return [(at, used)]
    return (previous + [(at, used)])[-cap:]


def firmware_upgrade(reply: dict) -> dict:
    """The hub's cached word on newer firmware.

    Probed on 2026-08-17: an up-to-date hub holds an EMPTY upgrade_info, so
    empty means current -- and a WAN-blocked hub holds empty forever, which
    is also the truth. The field names of a pending
    update are unknown until one exists; the plausible spellings are tried
    and the raw block rides along so nothing is lost if they all miss.
    """
    info = ((reply.get("getCloudConfig") or {}).get("cloud_config") or {}).get(
        "upgrade_info") or {}
    version = next((str(info[key]) for key in
                    ("firmware_version", "version", "fw_version")
                    if info.get(key)), None)
    return {"version": version, "raw": dict(info)}
