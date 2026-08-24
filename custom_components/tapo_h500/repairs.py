"""Conditions worth interrupting someone about, raised as Home Assistant issues.

The alternative is a debug log line nobody reads, and every one of these is
silent until footage is already lost or gone unrecorded: a hub at 99% is
overwriting its oldest recordings, a hub that stopped answering is recording
nothing while every entity keeps showing its last known value, a camera that
has gone quiet looks exactly like a quiet camera, and two cameras sharing a
name file their downloads over each other.

Camera tampering is the exception to that pattern and the reason it is here
anyway: it is reported, loudly, for thirty seconds, and then the sensor clears
and nobody who was not looking ever knows.

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
TAMPER_ISSUE = "camera_tampered"
MEDIA_ISSUE = "media_wedged"
DOWNLOADS_ISSUE = "downloads_failing"
RESTART_INEFFECTIVE_ISSUE = "restart_ineffective"

# Consecutive failures on one camera before it becomes a notice. One is a
# blip, two is a bad evening; three in a row with no success between them is
# a pipeline that will fail the fourth time too.
DOWNLOAD_FAIL_ALERT = 3


def _issue_id(entry_id: str, kind: str) -> str:
    return f"{kind}_{entry_id}"


def async_check(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Raise or clear every issue for one hub. Safe to call every poll.

    Each check clears its own issue when the condition has gone. One that
    never clears is worse than one that never appears.
    """
    # Six checks. This docstring has twice said "both of these" while listing
    # more than two, so the count is deliberately not restated below.
    _storage(hass, entry_id, coordinator)
    _reachable(hass, entry_id, coordinator)
    _unnamed_faces(hass, entry_id, coordinator)
    _silent_cameras(hass, entry_id, coordinator)
    _clashing_names(hass, entry_id, coordinator)
    _media(hass, entry_id, coordinator)
    _downloads_failing(hass, entry_id, coordinator)
    _restart_ineffective(hass, entry_id, coordinator)
    _tampered(hass, entry_id, coordinator)


def _tampered(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Say when a camera reported being interfered with.

    The one detection that must not be allowed to scroll past. Everything else
    here happened outside the house; this is somebody handling the camera
    itself, and if it is real then the recordings after it are the ones that
    will be missing.

    Its binary sensor holds for thirty seconds and clears -- right for a
    history graph, useless for a fact somebody needs to see whenever they next
    open Home Assistant. This stays until the detection ages out of the poll
    window, which is a day.

    An absolute time, deliberately, where diagnostics deliberately has none:
    this is for the owner and "someone touched your camera at some point" is
    not a usable thing to be told.
    """
    from homeassistant.util import dt as dt_util

    issue_id = _issue_id(entry_id, TAMPER_ISSUE)
    events = coordinator.tampered(LOOKBACK_SECONDS)
    if not events:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    camera, moment = events[0]
    when = dt_util.as_local(dt_util.utc_from_timestamp(moment))
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=TAMPER_ISSUE,
        translation_placeholders={
            "camera": camera,
            "when": when.strftime("%H:%M on %d %b"),
            # How many times, because once is a knock and repeatedly is not.
            "count": str(len(events)),
        },
    )


def _storage(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    # The name has to match what status.hub_readings emits exactly: a near
    # miss reads None every poll and silently retires the warning below.
    used_percent = coordinator.readings.get("storage_used_percent")
    issue_id = _issue_id(entry_id, STORAGE_ISSUE)
    # Unknown is not the same as fine. Say nothing rather than guessing.
    if used_percent is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
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
        # Fixable: the notice takes the name itself instead of pointing at
        # the Configure page. `data` is what the flow gets handed.
        is_fixable=True,
        data={"entry_id": entry_id, "face_id": str(top["id"])},
        severity=ir.IssueSeverity.WARNING,
        translation_key=UNNAMED_FACE_ISSUE,
        translation_placeholders={
            "face_id": str(top["id"]),
            "sightings": str(top.get("sightings", 0)),
            "cameras": ", ".join(top.get("cameras") or []) or "a camera",
            "others": str(len(frequent) - 1),
        },
    )


def _media(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Say when the hub has stopped serving recordings.

    The known failure: hours after a reboot, port 8800 starts accepting a
    connection and closing it before a single HTTP byte. Detections still
    arrive -- the control channel is fine -- so the first sign anyone gets
    is a notification without a photograph, and then a recording list that
    will not play. The handshake check sees it within fifteen minutes.

    Only the proven zero-byte signature alarms. "unreachable" already has
    its own issue via the failing poll, and "silent" is one slow reply away
    from crying wolf.
    """
    issue_id = _issue_id(entry_id, MEDIA_ISSUE)
    wedged = getattr(coordinator, "media_status", None) == "wedged"
    # The 2026-08-18 variant: sessions answer perfectly and carry nothing.
    # The handshake cannot see it; the downloads can.
    empty = bool(getattr(coordinator, "media_serving_empty", False))
    if not (wedged or empty):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=MEDIA_ISSUE,
    )


def _downloads_failing(hass: HomeAssistant, entry_id: str, coordinator) -> None:
    """Say when automatic downloads keep failing on a camera.

    Each failure is a warning in the log, and the next clip fails the same
    way for the same reason -- ffmpeg missing, the disk full, the media
    service refusing. The count resets on any success, so this names a
    pipeline that is broken NOW, and clears the moment one clip lands.
    """
    issue_id = _issue_id(entry_id, DOWNLOADS_ISSUE)
    failing = {name: count
               for name, count in getattr(
                   coordinator, "download_failures", {}).items()
               if count >= DOWNLOAD_FAIL_ALERT}
    if not failing:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=DOWNLOADS_ISSUE,
        translation_placeholders={
            "cameras": ", ".join(sorted(failing)),
            "count": str(max(failing.values())),
        },
    )


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str,
                                data: dict | None):
    """The form behind the one fixable issue: naming a face in place.

    The base class is imported here rather than at module top on purpose:
    repairs.py is imported by the coordinator on every poll, and
    homeassistant.components.repairs only exists once that component is
    loaded -- which it certainly is by the time somebody presses Fix.
    """
    from homeassistant.components.repairs import RepairsFlow

    import voluptuous as vol

    fix = data or {}

    class NameFaceFlow(RepairsFlow):
        """Ask for the name where the face is being talked about."""

        async def async_step_init(self, user_input: dict | None = None):
            if user_input is not None:
                name = str(user_input.get("name") or "").strip()
                coordinator = (self.hass.data.get(DOMAIN, {})
                               .get(DATA_HUBS, {})
                               .get(fix.get("entry_id")))
                if coordinator is not None and name:
                    from .const import CONF_FACE_NAMES
                    names = dict(coordinator.entry.options.get(
                        CONF_FACE_NAMES) or {})
                    names[str(fix.get("face_id"))] = name
                    # Through async_update_entry, so the existing options
                    # listener redraws every face surface -- the exact path
                    # the name_face service and the card already use. An
                    # empty answer names nobody and just closes the notice;
                    # the next check re-raises it while the face is unnamed.
                    self.hass.config_entries.async_update_entry(
                        coordinator.entry,
                        options={**coordinator.entry.options,
                                 CONF_FACE_NAMES: names})
                return self.async_create_entry(data={})
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({vol.Required("name"): str}),
                description_placeholders={
                    "face_id": str(fix.get("face_id", ""))},
            )

    return NameFaceFlow()


def _restart_ineffective(hass: HomeAssistant, entry_id: str,
                         coordinator) -> None:
    """Say when the automatic restart failed to cure the media failure.

    A reboot has cured every media failure this hub has ever shown; one
    that does not is a NEW failure, and quietly rebooting every six hours
    would mask it forever. The breaker has already paused the automation --
    this makes the pause, and the reason, visible. Clears itself the moment
    recordings actually serve again.
    """
    issue_id = _issue_id(entry_id, RESTART_INEFFECTIVE_ISSUE)
    if not getattr(coordinator, "auto_restart_broken", False):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass, DOMAIN, issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=RESTART_INEFFECTIVE_ISSUE,
    )
