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
    CONF_POLL_INTERVAL, DATA_HUBS, DETECTION_NAMES, DOMAIN, LOOKBACK_SECONDS,
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


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
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
            "download_failures": dict(getattr(
                coordinator, "_download_failures", {}) or {}),
        },
    }
