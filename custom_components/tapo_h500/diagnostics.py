"""Diagnostics download for a hub.

This integration talks to an undocumented protocol on one firmware, so a bug
report is usually unusable without knowing what the hub actually returned.
This is that, minus anything that identifies the installation.

Redaction is by allow-list, not by blocking known-bad keys. The hub's replies
are its own and change between firmwares, so a deny-list would leak whatever
the next version adds; here nothing reaches the file unless it was named.
"""
from __future__ import annotations

import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .clips import detection_types, hourly_baseline, start_of
from .const import (
    CONF_AUTO_DOWNLOAD, CONF_CONVERT_MP4, CONF_FACE_NAMES, CONF_KEEP_DOWNLOADS,
    CONF_POLL_INTERVAL, DETECTION_NAMES, LOOKBACK_SECONDS,
)

# Hub readings safe to include. Storage figures and firmware state are what
# most reports need; the cloud username, public key and any device identifier
# are deliberately absent and are not obtainable from this file.
#
# These are the keys `hub_readings` actually produces, and a test asserts that
# rather than trusting it. Six of them were near misses -- `storage_total` for
# `storage_total_gb`, `led_enabled` for `led_on`, `used_audio_slots` for
# `custom_sounds` -- so all three storage figures, the LED, face detection and
# the audio slots came out null in every diagnostics download ever taken.
# Nothing failed and nothing warned; the file simply said nothing, which is the
# way an allow-list fails.
SAFE_READINGS = (
    "storage_total_gb", "storage_free_gb", "storage_used_percent",
    "storage_healthy", "storage_status", "media_encrypted", "firmware_state",
    "auto_upgrade", "loop_recording", "led_on", "siren_volume",
    "siren_duration", "clock_offset", "timezone", "face_detection",
    "custom_sounds",
    # A hub that reboots itself explains a gap in recordings that would
    # otherwise be reported as a camera fault.
    "scheduled_reboot",
)

# What the hub says it is. Everything else in the record -- mac, dev_id,
# device_alias, oem_id -- identifies this installation and stays out.
SAFE_DEVICE = ("device_model", "sw_version", "hw_version")

# Per-camera fields safe to include. Aliases are the owner's own words and can
# name a room or a person, so they are replaced by their position instead.
SAFE_CAMERA = (
    "device_model", "hub_storage_enabled", "plan_24h_record",
    "ai_enhance_enabled", "wifi_backup_enabled", "battery_percent",
)


# How many paths of the hub's answer to describe. A hub with sixteen cameras
# and a firmware that has grown could otherwise fill the file.
SHAPE_LIMIT = 400


def _shape(value: Any, prefix: str = "", out: dict | None = None) -> dict:
    """Every path in the hub's answer, with the kind of thing at the end.

    Names and types, never values. The allow-list above is the right way to
    keep an installation out of a public bug report, and it has one cost: a
    field the parser does not know about is invisible here, so nobody can add
    it to the list because nobody knows it exists. That is exactly how
    `detect_status` went unnoticed until somebody dumped the JSON by hand.

    A path and a type give that away without giving anything else away. The
    fix for a key that turns out to matter is still to name it above, which is
    the point: this makes the list maintainable, it does not replace it.
    """
    out = {} if out is None else out
    if isinstance(value, dict):
        for key in value:
            if len(out) >= SHAPE_LIMIT:
                out["truncated"] = f"more than {SHAPE_LIMIT} paths"
                break
            _shape(value[key], f"{prefix}{key}.", out)
    elif isinstance(value, list):
        # One entry stands for the whole list: the siblings repeat its shape,
        # and the length is already the interesting part.
        if value:
            _shape(value[0], f"{prefix}[{len(value)}].", out)
        else:
            out[prefix.rstrip(".")] = "list(0)"
    else:
        out[prefix.rstrip(".")] = type(value).__name__
    return out


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    now = int(time.time())

    cameras = []
    for index, camera in enumerate(coordinator.cameras):
        clips = coordinator.clips_for(index)
        # Detections are summarised as counts per code rather than listed:
        # the codes are what a report is about, and the timestamps would map
        # someone's comings and goings.
        counts: dict[str, int] = {}
        for clip in clips:
            for code in detection_types(clip):
                label = DETECTION_NAMES.get(code, f"type {code}")
                counts[label] = counts.get(label, 0) + 1
        newest = max((start_of(clip) or 0) for clip in clips) if clips else None
        cameras.append({
            "index": index,
            **{key: camera.get(key) for key in SAFE_CAMERA},
            "recordings_in_window": len(clips),
            "detections_by_type": counts,
            # Relative, not absolute: "the newest clip is 300s old" answers the
            # same question as a wall-clock time without dating the household.
            "newest_recording_age": (now - newest) if newest else None,
            "typical_per_hour": round(
                hourly_baseline(clips, now, LOOKBACK_SECONDS), 2),
        })

    return {
        "options": {
            # The tuning that explains behaviour. No credentials live here.
            CONF_POLL_INTERVAL: entry.options.get(CONF_POLL_INTERVAL),
            CONF_AUTO_DOWNLOAD: entry.options.get(CONF_AUTO_DOWNLOAD),
            CONF_CONVERT_MP4: entry.options.get(CONF_CONVERT_MP4),
            CONF_KEEP_DOWNLOADS: entry.options.get(CONF_KEEP_DOWNLOADS),
            # How many faces are named, never who they are.
            "named_faces": len(entry.options.get(CONF_FACE_NAMES) or {}),
        },
        # Firmware and hardware revision, which is the first thing anyone
        # reading a bug report about an undocumented protocol wants to know.
        # Model-wide values: they identify the device type, never the
        # installation, and the rest of the record -- mac, dev_id, alias -- is
        # deliberately left out.
        "device": {key: coordinator.client.info.get(key)
                   for key in SAFE_DEVICE},
        "hub": {key: coordinator.readings.get(key) for key in SAFE_READINGS},
        # What the hub answered, described rather than quoted. This is the
        # only part of the file that can show a field nobody has named yet.
        "hub_answer_shape": _shape(getattr(coordinator, "raw_status", None)
                                   or {}),
        "cameras": cameras,
        "coordinator": {
            "update_interval": (coordinator.update_interval.total_seconds()
                                if coordinator.update_interval else None),
            "polls": getattr(coordinator, "_polls", None),
            "last_update_success": coordinator.last_update_success,
            "cameras_found": len(coordinator.cameras),
            # The wedge investigation's numbers: what the sentinel last said,
            # how many media sessions this process has opened, and any
            # consecutive download failures -- keyed by camera INDEX, because
            # aliases are the owner's own words and never leave in this file.
            "media_status": getattr(coordinator, "media_status", None),
            "media_sessions": getattr(
                coordinator.client, "_sessions", None),
            # How the last few went, so a report says whether the hub was
            # refusing sessions or answering them with nothing -- which are
            # different failures with different cures and read identically
            # from a session count alone.
            "session_health": getattr(
                coordinator.client, "session_health", None),
            "download_failures": dict(getattr(
                coordinator, "_download_failures", {}) or {}),
            # Every outage this process has seen, newest first, with what was
            # tried against it and how long it lasted. The whole point of a
            # bug report is the second half of that: "we restarted it and it
            # came back in four minutes" and "we restarted it and nothing
            # changed" are different reports, and neither survives being
            # remembered a week later.
            "wedge_log": coordinator.recovery_log(),
        },
    }
