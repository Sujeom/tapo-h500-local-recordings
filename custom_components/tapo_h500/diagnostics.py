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
SAFE_READINGS = (
    "storage_total", "storage_free", "storage_used", "storage_healthy",
    "storage_status", "media_encrypted", "firmware_state", "auto_upgrade",
    "loop_recording", "led_enabled", "siren_volume", "siren_duration",
    "clock_offset", "timezone", "face_detection_enabled", "used_audio_slots",
)

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
        "hub": {key: coordinator.readings.get(key) for key in SAFE_READINGS},
        "cameras": cameras,
        "coordinator": {
            "update_interval": (coordinator.update_interval.total_seconds()
                                if coordinator.update_interval else None),
            "polls": getattr(coordinator, "_polls", None),
            "last_update_success": coordinator.last_update_success,
            "cameras_found": len(coordinator.cameras),
        },
    }
