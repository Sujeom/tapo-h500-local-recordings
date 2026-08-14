"""Conditions worth interrupting someone about, raised as Home Assistant issues.

The alternative is a debug log line nobody reads, and every one of these is
silent until footage is already lost or gone unrecorded: a hub at 99% is
overwriting its oldest recordings, a hub that stopped answering is recording
nothing while every entity keeps showing its last known value, a camera that
has gone quiet looks exactly like a quiet camera, and two cameras sharing a
name file their downloads over each other.

Deliberately not raised: a single failed poll, which is normal on a busy
network and recovers by itself, and low free space in gigabytes, which means
nothing without knowing the disk size.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .clips import clashing_names
from .const import (
    CONF_SILENT_HOURS, DATA_HUBS, DEFAULT_SILENT_HOURS, DOMAIN,
    LOOKBACK_SECONDS, NAME_PROMPT_SIGHTINGS,
)

# Percent used at which the hub is about to start overwriting. Loop recording
# does not fail at 100%, it silently discards the oldest footage, so the
# warning has to arrive before that rather than at it.
STORAGE_WARN_PERCENT = 95

STORAGE_ISSUE = "storage_nearly_full"
UNREACHABLE_ISSUE = "hub_unreachable"
UNNAMED_FACE_ISSUE = "unnamed_face"
SILENT_CAMERA_ISSUE = "camera_silent"
CLASHING_NAMES_ISSUE = "clashing_camera_names"


def _issue_id(entry_id: str, kind: str) -> str:
    return f"{kind}_{entry_id}"


def async_check(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Raise or clear every issue for one hub. Safe to call every poll.

    Each check clears its own issue when the condition has gone. One that
    never clears is worse than one that never appears.
    """
    _storage(hass, entry_id, coordinator)
    _reachable(hass, entry_id, coordinator)
    _unnamed_faces(hass, entry_id, coordinator)
    _silent_cameras(hass, entry_id, coordinator)
    _clashing_names(hass, entry_id, coordinator)


def _storage(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    total = coordinator.readings.get("storage_total")
    free = coordinator.readings.get("storage_free")
    issue_id = _issue_id(entry_id, STORAGE_ISSUE)
    # Unknown is not the same as fine. Say nothing rather than guessing.
    if not total or free is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    used_percent = round((total - free) / total * 100)
    if used_percent < STORAGE_WARN_PERCENT:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=STORAGE_ISSUE,
        translation_placeholders={"used": str(used_percent)},
    )


def _reachable(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    issue_id = _issue_id(entry_id, UNREACHABLE_ISSUE)
    if coordinator.last_update_success:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=UNREACHABLE_ISSUE,
    )


def _clashing_names(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Warn when two cameras would write to the same folder.

    Downloads are filed under a slug of the camera's own name --
    <camera>/<date>/<time>.mp4 -- and that is deliberate: it makes "already
    downloaded" a path check rather than an index that could disagree with
    the files on disk.

    It also means two cameras called the same thing share a folder, and then
    "already downloaded" is answered for one camera by the other's recording.
    Two hubs make that likely rather than theoretical.

    Reported rather than worked around. Renaming a camera in the Tapo app
    fixes it in seconds; putting the hub into the path would orphan every
    recording anyone has already downloaded, to fix a case most installations
    do not have.
    """
    issue_id = _issue_id(entry_id, CLASHING_NAMES_ISSUE)
    every_camera = [
        camera
        for hub in (hass.data.get(DOMAIN, {}).get(DATA_HUBS, {}) or {}).values()
        for camera in getattr(hub, "cameras", [])
    ]
    clashing = clashing_names(every_camera, coordinator.cameras)
    if not clashing:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=CLASHING_NAMES_ISSUE,
        translation_placeholders={"cameras": ", ".join(clashing)},
    )


def _silent_cameras(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Say when a camera has stopped producing anything at all.

    Worth interrupting someone about because the failure is invisible
    otherwise: a camera off the Wi-Fi or flat looks identical to a quiet one,
    all its entities keep showing their last value, and the usual way to find
    out is needing the footage. The hub reports no online flag to check
    instead -- 16 fields in the paired-device record and not one of them says.
    """
    issue_id = _issue_id(entry_id, SILENT_CAMERA_ISSUE)
    hours = coordinator.entry.options.get(
        CONF_SILENT_HOURS, DEFAULT_SILENT_HOURS)
    try:
        threshold = min(max(3600, int(hours) * 3600), LOOKBACK_SECONDS)
    except (TypeError, ValueError):
        threshold = DEFAULT_SILENT_HOURS * 3600
    quiet = coordinator.silent_cameras(threshold)
    if not quiet:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=SILENT_CAMERA_ISSUE,
        translation_placeholders={
            "cameras": ", ".join(quiet),
            "hours": str(threshold // 3600),
        },
    )


def _unnamed_faces(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Suggest naming a face the hub keeps seeing.

    One issue for all of them rather than one each: a busy street would
    otherwise fill the repairs page with numbers, which is the opposite of
    helpful. The issue names the most-seen face and says how many others are
    waiting.
    """
    issue_id = _issue_id(entry_id, UNNAMED_FACE_ISSUE)
    named = coordinator.face_names
    frequent = sorted(
        (face for face_id, face in coordinator.faces_seen().items()
         if face_id not in named
         and face.get("sightings", 0) >= NAME_PROMPT_SIGHTINGS),
        key=lambda face: face.get("sightings", 0), reverse=True)
    if not frequent:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    top = frequent[0]
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=UNNAMED_FACE_ISSUE,
        translation_placeholders={
            "face_id": str(top["id"]),
            "sightings": str(top.get("sightings", 0)),
            "cameras": ", ".join(top.get("cameras") or []) or "a camera",
            "others": str(len(frequent) - 1),
        },
    )
