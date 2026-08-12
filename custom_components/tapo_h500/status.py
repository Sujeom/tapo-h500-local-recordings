"""Reading the hub's status responses.

Pure functions over what the hub returns, so they can be tested without a hub
or a Home Assistant install. Every key here came from an observed response —
see docs/protocol-notes.md.
"""
from __future__ import annotations

import re

# One round trip covers all of these. Each was confirmed to return data on an
# H500 running firmware 1.3.20; the other 55 getters pytapo knows about do not.
HUB_STATUS_REQUESTS = (
    ("getSdCardStatus", {"harddisk_manage": {"table": ["hd_info"]}}),
    ("getSirenStatus", {"siren": {}}),
    ("getFirmwareUpdateStatus", {"cloud_config": {"name": "upgrade_status"}}),
    ("getLedStatus", {"led": {"name": ["config"]}}),
    ("getCircularRecordingConfig", {"harddisk_manage": {"name": "harddisk"}}),
    ("getMediaEncrypt", {"cet": {"name": ["media_encrypt"]}}),
    ("getDeviceIpAddress", {"network": {"name": ["wan"]}}),
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


def _on(value) -> bool | None:
    if value is None:
        return None
    return str(value).lower() in ("on", "1", "true", "enabled")


def hub_readings(status: dict) -> dict:
    """Flatten the hub's status into the values entities are built from."""
    hd = disk(status)
    free = gigabytes(hd.get("video_free_space"))
    total = gigabytes(hd.get("video_total_space"))
    used_percent = None
    if free is not None and total:
        used_percent = round((total - free) / total * 100, 1)
    siren = status.get("getSirenStatus") or {}
    return {
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
    }
