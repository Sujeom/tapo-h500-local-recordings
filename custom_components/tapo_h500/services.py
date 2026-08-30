"""The thirteen services this integration offers.

Their own module because they are their own thing: `__init__.py` is where an
entry is set up and taken down, and 540 lines of request handling in the
middle of that made both harder to find. Home Assistant looks for exactly two
names in the package body -- setup and unload -- and everything here is
reached through one call from there.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .backup import (
    USER_OPTIONS, merge_names, merge_ranks, merge_settings,
    restored_options, snapshot,
)
from .clips import (
    describe_detection, detection_types, distinct, end_for_start, end_of,
    event_type, face_ids, highlights, start_of, summarise, window_dates,
)
from .const import (
    CONF_CARD_DAYS, CONF_FACE_NAMES, CONF_NIGHT_END, CONF_NIGHT_START,
    DEFAULT_CARD_DAYS, DEFAULT_NIGHT_END, DEFAULT_NIGHT_START,
    CONF_CONVERT_MP4, DEFAULT_CONVERT_MP4,
    DESCRIBE_PROMPT, DOMAIN, SERVICE_BACKUP_NAMES, SERVICE_CLASSIFY_DOWNLOADS,
    SERVICE_DAILY_SUMMARY, SERVICE_DELETE_RECORDING,
    SERVICE_DESCRIBE_RECORDING, SERVICE_DOWNLOAD_RECORDING,
    SERVICE_EXPORT_RECORDING, SERVICE_FIND_FACE, SERVICE_FORMAT_HUB_STORAGE,
    SERVICE_LIST_RECORDINGS, SERVICE_NAME_FACE, SERVICE_RESTORE_NAMES,
    SERVICE_SNOOZE,
)
from .coordinator import H500Coordinator
from .media import (
    archive_face_search, async_classify_downloads, async_delete_clip,
    async_download_clip, async_export, clip_path,
    describe, existing_clip, media_content_id, scan_downloaded,
)
from .preview import preview_url

_LOGGER = logging.getLogger(__name__)


# Every index and timestamp on these forms. A negative camera index or
# start time is not a request anything here can serve.
NONNEGATIVE_INT = vol.All(vol.Coerce(int), vol.Range(min=0))

ENTRY_SCHEMA = {
    vol.Required("config_entry_id"): cv.string,
    vol.Optional("camera_index", default=0): NONNEGATIVE_INT,
}
LIST_SCHEMA = vol.Schema({
    **ENTRY_SCHEMA,
    vol.Optional("start_date"): cv.string,
    vol.Optional("end_date"): cv.string,
})
DOWNLOAD_SCHEMA = vol.Schema({
    **ENTRY_SCHEMA,
    vol.Required("start_time"): NONNEGATIVE_INT,
    # Optional: the notification's Save button knows the event's start and
    # nothing else, so a missing end is looked up in the hub's clip index.
    vol.Optional("end_time"): NONNEGATIVE_INT,
    vol.Optional("convert_to_mp4"): cv.boolean,
})
DELETE_SCHEMA = vol.Schema({
    **ENTRY_SCHEMA,
    vol.Required("start_time"): NONNEGATIVE_INT,
})
EXPORT_SCHEMA = vol.Schema({
    **ENTRY_SCHEMA,
    vol.Required("start_time"): NONNEGATIVE_INT,
    # No default. Copying files somewhere is not a thing to guess at, and an
    # unset destination should fail loudly rather than write to a surprise.
    vol.Required("destination"): cv.string,
})
FIND_FACE_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    # Either a name you gave someone or the hub's own id, because the useful
    # one differs by caller: an automation has the id from an event, a person
    # typing into Developer Tools has the name.
    vol.Required("who"): cv.string,
})
SUMMARY_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Optional("hours", default=24): vol.All(vol.Coerce(int),
                                               vol.Range(min=1, max=168)),
})
DESCRIBE_SCHEMA = vol.Schema({
    **ENTRY_SCHEMA,
    vol.Required("start_time"): NONNEGATIVE_INT,
    # Which conversation agent or AI task entity to ask. Required rather than
    # guessed: an installation may have several, and picking one silently
    # would send a picture of someone's doorstep to whichever happened to be
    # first -- possibly a cloud service.
    vol.Required("agent_id"): cv.string,
    vol.Optional("prompt", default=DESCRIBE_PROMPT): cv.string,
})
NAME_FACE_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    # The hub reports ids as numbers; accept either spelling and store one.
    vol.Required("face_id"): vol.All(vol.Coerce(str), vol.Length(min=1)),
    # Omitted or empty clears the name rather than storing a blank one.
    vol.Optional("name", default=""): cv.string,
})
BACKUP_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
})
# Validated rather than trusted. This writes straight into the config entry's
# options, and a restore is exactly the moment somebody pastes a hand-edited
# blob in: an id that is not a string or a rank that is not a number would sit
# there until something else tripped over it, a long way from here.
RESTORE_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("face_names"): vol.Schema({cv.string: cv.string}),
    vol.Optional("camera_order"): vol.Schema({
        cv.string: vol.All(vol.Coerce(int), vol.Range(min=0, max=20))}),
    # The authored settings, filtered to the known keys again at merge time
    # -- a restore is exactly the moment somebody pastes a hand-edited blob.
    vol.Optional("settings"): vol.Schema({cv.string: object}),
    # Merging by default. Replacing is the destructive one, and losing a name
    # means going back through the photographs to work out who a
    # twelve-digit number was.
    vol.Optional("replace", default=False): cv.boolean,
})
SNOOZE_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    # 0 cancels a snooze already running. Omitted means indefinitely, which is
    # what the switch does when flipped by hand.
    vol.Optional("minutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
})
CLASSIFY_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    # 31 is as far as the recording-window search was ever verified.
    vol.Optional("days", default=31): vol.All(vol.Coerce(int),
                                              vol.Range(min=1, max=31)),
})
FORMAT_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("confirm"): vol.All(cv.boolean, vol.Equal(True)),
})

# Removed when the last hub unloads. Service names only: this had collected
# SIGNAL_FACES_CHANGED, RELOAD_ON_CHANGE, DESCRIBE_PROMPT and CONF_FACE_NAMES
# over time, none of which name a service. Nothing broke -- has_service simply
# answered no -- which is why it went unnoticed, and it made the tuple useless
# as a statement of what gets cleaned up.
SERVICES = (
    SERVICE_LIST_RECORDINGS,
    SERVICE_DOWNLOAD_RECORDING,
    SERVICE_DELETE_RECORDING,
    SERVICE_FORMAT_HUB_STORAGE,
    SERVICE_NAME_FACE,
    SERVICE_DESCRIBE_RECORDING,
    SERVICE_DAILY_SUMMARY,
    SERVICE_FIND_FACE,
    SERVICE_EXPORT_RECORDING,
    SERVICE_SNOOZE,
    SERVICE_BACKUP_NAMES,
    SERVICE_RESTORE_NAMES,
    SERVICE_CLASSIFY_DOWNLOADS,
)


def _public_camera(camera):
    return {
        "alias": camera.get("alias") or camera.get("device_name") or "Camera",
        "model": camera.get("device_model"),
    }


def _coordinator(hass, entry_id) -> H500Coordinator:
    """The hub an action was aimed at.

    The actions are registered once, whether or not any hub is loaded, so
    every one of them arrives here first and this is where "there is no such
    hub" is answered. A validation error, not a failure: the card sends
    whatever entry id it stored, and a stale one means "reconfigure the
    card" rather than "the hub is broken".
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    coordinator = getattr(entry, "runtime_data", None) if entry else None
    if coordinator is None:
        raise ServiceValidationError(
            f"No Tapo H500 hub is set up for config entry {entry_id}")
    return coordinator


async def _resolve(hass, call: ServiceCall):
    """The coordinator and the selected camera for a service call."""
    coordinator = _coordinator(hass, call.data["config_entry_id"])
    try:
        camera = await hass.async_add_executor_job(
            coordinator.client.camera_at, call.data["camera_index"])
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except Exception as err:
        raise HomeAssistantError("Unable to list H500 cameras") from err
    return coordinator, camera


def async_register(hass: HomeAssistant) -> None:
    """Register all thirteen. Called once, from the first entry set up."""
    async def list_recordings(call: ServiceCall):
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        start_date = call.data.get("start_date")
        end_date = call.data.get("end_date")
        days = None
        if start_date is None and end_date is None:
            # No window asked for: the Configure page's "days to show"
            # decides, so eight card editors defer to one setting. Read at
            # call time -- a reload would buy a login for a number.
            days = max(1, min(30, int(coordinator.entry.options.get(
                CONF_CARD_DAYS, DEFAULT_CARD_DAYS))))
            start_date, end_date = window_dates(
                days, int(dt_util.utcnow().timestamp()))
        try:
            camera, recordings = await hass.async_add_executor_job(
                coordinator.client.recordings, call.data["camera_index"],
                start_date, end_date)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except Exception as err:
            raise HomeAssistantError("Unable to list H500 recordings") from err

        clips = [
            (start_of(clip), end_of(clip), clip) for clip in recordings
        ]
        clips = [item for item in clips if item[0] is not None and item[1] is not None]
        on_disk = await hass.async_add_executor_job(
            scan_downloaded, hass, camera, [start for start, _, _ in clips])
        return {
            "camera": _public_camera(camera),
            # How many days this listing covers, when the option decided. A
            # card that sent no window captions itself with this instead of
            # its own default.
            "days": days,
            # The shared name map, so a card shows names without being told
            # them. A card may still override it locally.
            "face_names": coordinator.face_names,
            # So a caller can offer a camera picker without probing indexes.
            "cameras": [
                {"index": position, **_public_camera(item)}
                for position, item in enumerate(coordinator.cameras)
            ],
            "recordings": [
                {
                    "start_time": start,
                    "end_time": end,
                    "duration": end - start,
                    "event_type": event_type(clip),
                    "video_type": clip.get("video_type"),
                    # What the hub says actually triggered it. video_type is
                    # "2" for everything, so this is the useful one.
                    "detection": describe_detection(clip),
                    "alarm_type": clip.get("alarm_type"),
                    "detection_types": detection_types(clip),
                    "face_ids": face_ids(clip),
                    "downloaded": start in on_disk,
                    # A clip still only on the hub gets a preview URL rather
                    # than nothing. It is generated when something actually
                    # asks for the image, not here, so listing stays one call.
                    **(describe(hass, on_disk[start]) if start in on_disk else {
                        "thumbnail": preview_url(
                            hass, call.data["config_entry_id"],
                            call.data["camera_index"], start),
                    }),
                }
                for start, end, clip in sorted(clips, key=lambda item: item[0])
            ],
        }

    async def download_recording(call: ServiceCall):
        coordinator, camera = await _resolve(hass, call)
        start_time = call.data["start_time"]
        end_time = call.data.get("end_time")
        detected: list[int] = []
        if end_time is None:
            # Ask the hub which recording starts there. The window is a few
            # seconds wide only to absorb the one-second index tolerance.
            clips = await hass.async_add_executor_job(
                coordinator.client.recent, camera,
                start_time - 2, start_time + 2)
            end_time = end_for_start(clips, start_time)
            if end_time is None:
                raise ServiceValidationError(
                    "No indexed recording starts at that time -- if it just "
                    "happened, the hub may still be recording it")
            # The lookup answered with the whole clip record, so its
            # classification rides along to the sidecar for free.
            detected = next(
                (detection_types(clip) for clip in clips
                 if abs((start_of(clip) or 0) - start_time) <= 1), [])
        if end_time <= start_time:
            raise ServiceValidationError("end_time must be after start_time")
        convert = call.data.get(
            "convert_to_mp4",
            coordinator.entry.options.get(CONF_CONVERT_MP4, DEFAULT_CONVERT_MP4),
        )
        result = await async_download_clip(
            hass, coordinator.client, camera, start_time, end_time, convert,
            detected=detected or None)
        coordinator.async_update_listeners()
        return result

    async def delete_recording(call: ServiceCall):
        _, camera = await _resolve(hass, call)
        removed = await async_delete_clip(hass, camera, call.data["start_time"])
        if not removed:
            raise ServiceValidationError(
                "No downloaded copy of that recording was found")
        return {"removed": removed}

    async def format_hub_storage(call: ServiceCall):
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        _LOGGER.warning("Erasing all recordings on the H500 at %s",
                        coordinator.client.host)
        try:
            await hass.async_add_executor_job(coordinator.client.format_storage)
        except Exception as err:
            raise HomeAssistantError(
                f"The H500 refused to format its storage: {err}") from err
        return {"formatted": True}

    async def name_face(call: ServiceCall):
        """Give a hub face id a name, or clear it by passing none.

        Written to the config entry's options, which is what the per-face
        sensors and every card read, so one edit reaches all of them. Home
        Assistant reloads the entry on an options change, which is how a newly
        named face gains its sensor.
        """
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        face_id = str(call.data["face_id"])
        name = (call.data.get("name") or "").strip()
        names = dict(coordinator.entry.options.get(CONF_FACE_NAMES) or {})
        if name:
            names[face_id] = name
        else:
            names.pop(face_id, None)
        hass.config_entries.async_update_entry(
            coordinator.entry,
            options={**coordinator.entry.options, CONF_FACE_NAMES: names})
        return {"face_id": face_id, "name": name or None,
                "named": sorted(names)}

    async def describe_recording(call: ServiceCall):
        """Ask a vision model what is in a recording's own frame.

        Nothing is sent anywhere unless this is called with an explicit agent,
        so an installation with no AI configured is unaffected and no picture
        leaves the house by default.
        """
        coordinator, camera = await _resolve(hass, call)
        start_time = call.data["start_time"]
        thumbnail = clip_path(hass, camera, start_time, ".jpg")
        if not await hass.async_add_executor_job(thumbnail.is_file):
            raise ServiceValidationError(
                "No thumbnail for that recording yet. The hub indexes a clip "
                "only once it has finished, and the thumbnail is written when "
                "it downloads.")

        agent_id = call.data["agent_id"]
        prompt = call.data["prompt"]
        # ai_task is the current surface and conversation the older one. Try
        # the one that matches the entity given rather than guessing, and say
        # plainly when neither is available instead of failing obscurely.
        domain = agent_id.split(".", 1)[0]
        if domain == "ai_task" and hass.services.has_service("ai_task",
                                                             "generate_data"):
            result = await hass.services.async_call(
                "ai_task", "generate_data",
                {"task_name": "Describe H500 recording", "entity_id": agent_id,
                 "instructions": prompt,
                 "attachments": [{"media_content_id":
                                  media_content_id(hass, thumbnail),
                                  "media_content_type": "image/jpeg"}]},
                blocking=True, return_response=True)
            description = (result or {}).get("data")
        elif hass.services.has_service("conversation", "process"):
            result = await hass.services.async_call(
                "conversation", "process",
                {"agent_id": agent_id, "text": prompt},
                blocking=True, return_response=True)
            description = (((result or {}).get("response") or {})
                           .get("speech", {}).get("plain", {}).get("speech"))
        else:
            raise HomeAssistantError(
                "No AI service is available. Configure a conversation agent or "
                "an AI task entity, then pass its entity id as agent_id.")

        return {"start_time": start_time, "description": description,
                "agent_id": agent_id}

    async def daily_summary(call: ServiceCall):
        """One sentence per camera for the period.

        A service, not a schedule. Nothing is sent unless something calls it,
        so the digest is off until someone builds an automation for it -- a
        summary nobody asked for is what makes people mute an integration.
        The phrasing is shared with the voice answer so the two cannot
        describe the same day differently.
        """
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        window = call.data["hours"] * 3600
        # Qualified only where two cameras share a name, which one hub can
        # manage on its own if somebody names them that way -- a dictionary
        # keyed on the name would drop one and read as though it did not
        # exist.
        cameras = list(enumerate(coordinator.cameras))
        labels = distinct([
            (camera.get("alias") or f"Camera {index}", f"camera {index}")
            for index, camera in cameras])
        per_camera = {label: coordinator.clips_for(index)
                      for label, (index, _) in zip(labels, cameras)}
        now = int(dt_util.utcnow().timestamp())
        options = coordinator.entry.options
        return {
            "hours": call.data["hours"],
            "summary": summarise(per_camera, now, window),
            # What was different, which is what anybody reading a digest is
            # actually after. Usually empty, and that is the point: a list
            # that always has something in it says nothing.
            "highlights": highlights(
                per_camera, now, window,
                options.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
                options.get(CONF_NIGHT_END, DEFAULT_NIGHT_END)),
        }

    async def find_face(call: ServiceCall):
        """Every recording a person appears in, newest first.

        face_ids has been on every clip since the beginning and nothing could
        search it: the media browser groups by camera and date only, so
        "show me every clip with Alice" meant reading ids by eye.
        """
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        wanted = str(call.data["who"]).strip()
        names = coordinator.face_names
        # A name resolves to possibly several ids -- the hub clusters the same
        # person more than once when lighting differs, and both may be named.
        ids = {face_id for face_id, name in names.items()
               if name.casefold() == wanted.casefold()} or {wanted}

        matches = []
        for index, camera in enumerate(coordinator.cameras):
            for clip in coordinator.clips_for(index):
                if not ids & {str(face) for face in face_ids(clip)}:
                    continue
                start, end = start_of(clip), end_of(clip)
                if start is not None:
                    matches.append((index, camera, clip, start, end))

        def _describe_matches():
            """Blocking: existing_clip stats the media directory per clip."""
            return [{
                "camera": camera.get("alias") or f"Camera {index}",
                "camera_index": index,
                "start_time": start,
                "end_time": end,
                "detection": describe_detection(clip),
                "downloaded": existing_clip(hass, camera, start) is not None,
            } for index, camera, clip, start, end in matches]

        found = await hass.async_add_executor_job(_describe_matches)

        def _archive_matches():
            """Sidecar hits from beyond the hub's one-day index."""
            live = {(item["camera_index"], item["start_time"])
                    for item in found}
            older = []
            for index, camera in enumerate(coordinator.cameras):
                for hit in archive_face_search(hass, camera, ids):
                    if (index, hit["start_time"]) in live:
                        continue
                    older.append({
                        "camera": camera.get("alias") or f"Camera {index}",
                        "camera_index": index,
                        "start_time": hit["start_time"],
                        "end_time": None,
                        "detection": describe_detection(
                            {"events_1": sum(1 << (code - 1) for code in
                                             hit["detection_types"])}),
                        "downloaded": True,
                    })
            return older

        found += await hass.async_add_executor_job(_archive_matches)
        found.sort(key=lambda item: item["start_time"] or 0, reverse=True)
        return {"who": wanted, "face_ids": sorted(ids),
                "count": len(found), "recordings": found}

    async def export_recording(call: ServiceCall):
        """Copy a downloaded clip somewhere retention cannot reach."""
        _, camera = await _resolve(hass, call)
        return await async_export(hass, camera, call.data["start_time"],
                                  call.data["destination"])

    async def backup_names(call: ServiceCall):
        """Hand back everything that was typed in rather than measured.

        Face names and the camera layout are the only state here a hub cannot
        reproduce. Recordings live on the hub, settings live on the hub, and
        every sensor is derived. These two came out of somebody's head --
        months of looking at photographs to work out who a twelve-digit number
        is -- and they live on the config entry, so deleting the entry takes
        them with it and nothing warns first.

        Shaped so the answer can be pasted straight into restore_names.
        """
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        return snapshot(coordinator.face_names, coordinator.camera_ranks,
                        dict(coordinator.entry.options))

    async def classify_downloads(call: ServiceCall):
        """Write missing sidecars for the archive that predates them.

        One detection-log query per camera-day that has an unclassified
        clip, sequential, against a hub that must not be flooded -- and a
        day already covered costs it nothing, so re-running is cheap. A
        clip the log no longer remembers stays unclassified rather than
        guessed.
        """
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        days = call.data["days"]
        totals = {"scanned": 0, "written": 0, "days_queried": 0}
        for camera in coordinator.cameras:
            result = await async_classify_downloads(
                hass, coordinator.client, camera, days)
            for key in totals:
                totals[key] += result[key]
        return totals

    async def restore_names(call: ServiceCall):
        """Put a backup back, merging by default.

        Merging rather than replacing unless asked, because the common case is
        restoring an old backup onto an entry that has since learned a few
        more names, and a replace there quietly discards them.
        """
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        replace = call.data["replace"]
        names = merge_names(coordinator.face_names,
                            call.data["face_names"], replace)
        # None, not {}: a backup taken before the layout existed carries no
        # camera_order at all, and must not be read as "the layout is empty".
        supplied = call.data.get("camera_order")
        ranks = None if supplied is None else merge_ranks(
            coordinator.camera_ranks, supplied, replace)
        settings = merge_settings(
            {key: value for key, value in coordinator.entry.options.items()
             if key in USER_OPTIONS},
            call.data.get("settings"))
        hass.config_entries.async_update_entry(
            coordinator.entry,
            options=restored_options(coordinator.entry.options, names, ranks,
                                     settings=settings))
        return {"restored": len(names), "face_names": names,
                "camera_order": ranks if ranks is not None
                else coordinator.camera_ranks}

    async def snooze(call: ServiceCall):
        """Mute notifications for a while, without disabling the automation.

        Turning the automation off is the alternative, and it is a thing
        people forget to turn back on. Nothing stops recording, downloading or
        firing events -- footage during a snooze is the footage most likely to
        be wanted afterwards. Only the automation reads this, and only if it
        was told to.
        """
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        minutes = call.data.get("minutes")
        until = coordinator.snooze(None if minutes is None else minutes * 60)
        return {
            "snoozed": coordinator.snoozed,
            # Null for an indefinite snooze, which has no end to report.
            "until": (dt_util.utc_from_timestamp(until).isoformat()
                      if until not in (None, float("inf")) else None),
        }

    for service, handler, schema in (
        (SERVICE_LIST_RECORDINGS, list_recordings, LIST_SCHEMA),
        (SERVICE_DOWNLOAD_RECORDING, download_recording, DOWNLOAD_SCHEMA),
        (SERVICE_CLASSIFY_DOWNLOADS, classify_downloads, CLASSIFY_SCHEMA),
        (SERVICE_DELETE_RECORDING, delete_recording, DELETE_SCHEMA),
        (SERVICE_FORMAT_HUB_STORAGE, format_hub_storage, FORMAT_SCHEMA),
        (SERVICE_NAME_FACE, name_face, NAME_FACE_SCHEMA),
        (SERVICE_DESCRIBE_RECORDING, describe_recording, DESCRIBE_SCHEMA),
        (SERVICE_DAILY_SUMMARY, daily_summary, SUMMARY_SCHEMA),
        (SERVICE_FIND_FACE, find_face, FIND_FACE_SCHEMA),
        (SERVICE_EXPORT_RECORDING, export_recording, EXPORT_SCHEMA),
        (SERVICE_SNOOZE, snooze, SNOOZE_SCHEMA),
        (SERVICE_BACKUP_NAMES, backup_names, BACKUP_SCHEMA),
        (SERVICE_RESTORE_NAMES, restore_names, RESTORE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, service, handler, schema=schema,
            supports_response=SupportsResponse.ONLY,
        )
