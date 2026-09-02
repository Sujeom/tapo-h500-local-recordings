"""Doorbell and motion events for each paired camera."""
from __future__ import annotations

from .models import Camera

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .clips import (
    describe_detection, detection_types, end_of, face_ids, hub_label,
    notable, start_of,
)
from .const import (
    CONF_CONVERT_MP4, CONF_NIGHT_END, CONF_NIGHT_START, DEFAULT_CONVERT_MP4,
    DEFAULT_NIGHT_END, DEFAULT_NIGHT_START, DOMAIN, EVENT_TYPES,
)
from .coordinator import H500Coordinator
from .entity import add_cameras_as_they_appear, H500Entity
from .media import clip_path, signed_url
from .preview import preview_url

# Unlimited: nothing here polls the hub. Every value comes from the
# coordinator's one poll, so there is nothing to serialise.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    add_cameras_as_they_appear(
        coordinator, entry, async_add_entities,
        lambda index, camera: [H500ActivityEvent(coordinator, index, camera)])


class H500ActivityEvent(H500Entity, EventEntity):
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "activity"

    def __init__(self, coordinator: H500Coordinator, index: int,
                 camera: Camera) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_activity"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.coordinator.signal("event", self.index), self._handle))

    def _known_faces(self, entry: dict) -> list[str]:
        """The names of any recognised faces in this detection.

        Sorted so two people arriving together always read the same way round
        rather than in whatever order the hub listed them.
        """
        names = self.coordinator.face_names
        return sorted(
            {names[str(face)] for face in face_ids(entry) if str(face) in names})

    def _notable(self, entry: dict, start_time: int | None) -> bool:
        """Whether this is an unfamiliar face during the configured night."""
        if start_time is None:
            return False
        options = self.coordinator.entry.options
        local = dt_util.as_local(dt_util.utc_from_timestamp(start_time))
        return notable(
            entry, local.hour,
            options.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
            options.get(CONF_NIGHT_END, DEFAULT_NIGHT_END))

    def _own_frame(self, start_time: int | None) -> str | None:
        """Signed URL for the thumbnail of the clip this event refers to."""
        if start_time is None:
            return None
        try:
            return signed_url(
                self.hass, clip_path(self.hass, self.camera, start_time, ".jpg"))
        except Exception:  # noqa: BLE001 - an attribute is never worth failing on
            return None

    def _preview(self, start_time: int | None) -> str | None:
        """A URL that produces this event's frame whether or not it downloaded.

        `image` above addresses a file, and that file exists only once a
        download has written it -- so it 404s for every clip that was never
        downloaded, and for every clip at all until the hub finishes
        recording. Anything putting it straight into a notification therefore
        shows an empty picture some of the time, with nothing to distinguish
        that from a picture that has not arrived yet.

        This addresses the preview endpoint instead, which fetches the frame
        from the hub on demand and caches it on disk after the first look. It
        is what the recordings card already uses for clips it has not
        downloaded.
        """
        if start_time is None:
            return None
        try:
            return preview_url(self.hass, self.coordinator.entry.entry_id,
                               self.index, start_time)
        except Exception:  # noqa: BLE001 - an attribute is never worth failing on
            return None

    def _video(self, start_time: int | None) -> str | None:
        """This event's own recording, as a signed path.

        The suffix is the one the download was told to write -- ffmpeg
        remuxes to `.mp4` unless that is turned off, in which case the raw
        `.ts` is kept. Read from the options rather than by looking on disk,
        because this runs on the event loop.

        Like `image`, this addresses a downloaded file and does not check it
        is there. Unlike `image` there is no on-demand fallback: the hub will
        produce a frame for a clip nobody kept, but not the clip itself.
        """
        if start_time is None:
            return None
        try:
            convert = self.coordinator.entry.options.get(
                CONF_CONVERT_MP4, DEFAULT_CONVERT_MP4)
            return signed_url(self.hass, clip_path(
                self.hass, self.camera, int(start_time),
                ".mp4" if convert else ".ts"))
        except Exception:  # noqa: BLE001 - an attribute is never worth failing on
            return None

    @callback
    def _handle(self, kind: str, entry: dict) -> None:
        start_time = start_of(entry)
        end_time = end_of(entry)
        image = self._own_frame(start_time)
        preview = self._preview(start_time)
        video = self._video(start_time)
        self._trigger_event(kind, {
            # Which paired-list position this camera is: what
            # tapo_h500.download_recording calls camera_index, so a
            # notification button can name the clip it is about.
            "camera_index": self.index,
            "start_time": start_time,
            "end_time": end_time,
            "duration": (end_time - start_time)
            if start_time is not None and end_time is not None else None,
            # The hub's own label, kept raw so unrecognised types stay visible.
            "hub_type": hub_label(entry),
            # From the detection log: what actually triggered the recording.
            # hub_type is "2" for everything, so these are the useful ones.
            "detection": describe_detection(entry),
            "alarm_type": entry.get("alarm_type"),
            "detection_types": detection_types(entry),
            # A number per recognised face. The hub offers no name and no
            # image, but the id is stable enough to match in an automation.
            "face_ids": face_ids(entry),
            # ...and those numbers resolved through the hub's own name map.
            #
            # Only faces that have been named. An automation cannot reach the
            # config entry to do this lookup itself, and an unnamed face read
            # aloud as "Face 123456789012 is at the door" is worse than saying
            # "a person" -- the id belongs in face_ids, not in a sentence.
            "faces": self._known_faces(entry),
            # An unfamiliar face at night: the one combination worth a
            # different alarm sound. Derived here so an automation does not
            # have to re-implement a window that wraps midnight.
            "notable": self._notable(entry, start_time),
            # This event's own recording, beside `image`, its own still.
            "video": video,
            # This event's OWN frame, addressed by its timestamp.
            #
            # The camera entity deliberately serves whatever thumbnail is
            # newest, which is right for a camera and wrong for an event: ask
            # it two minutes later, after another clip has landed, and it
            # answers with the wrong picture. Measured across 49 detections on
            # this hub, a detection's start time is exactly the clip's start
            # time, so the path is computable here and stays pinned to this
            # event no matter what arrives afterwards.
            #
            # The file does not exist yet -- the hub is still recording -- so
            # this 404s until the download lands. Signing does not check for
            # the file, and the URL is good for 12 hours.
            "image": image,
            # The same frame, fetched on demand rather than read off disk.
            # `image` is the downloaded file and 404s until there is one;
            # this one produces a picture for a clip that was never
            # downloaded at all, which is what a notification wants.
            "preview": preview,
            # Whether notifications were muted when this fired.
            #
            # "There was activity and I got no message" has exactly two
            # answers on this side -- no event, or an event while snoozed --
            # and neither was visible after the fact. The event entity's own
            # history answers the first; without this it could not answer the
            # second, and the question went to the automation traces, which
            # are kept for a few runs and not at all after a restart.
            #
            # Recorded on the event rather than read from the switch later:
            # a snooze that has since expired is exactly the case that needs
            # explaining.
            "snoozed": self.coordinator.snoozed,
        })
        # Also on the event bus, which is what the logbook can describe and
        # what an automation can trigger on without naming an entity. The
        # entity remains the primary surface; this carries the same facts.
        self.hass.bus.async_fire(f"{DOMAIN}_event", {
            "entity_id": self.entity_id,
            # The camera's own alias, which the entity already holds. Reading
            # it back off the device registry would be the same string by a
            # longer route, and None before the registry has caught up.
            "name": self.camera.get("alias") or "Camera",
            "type": kind,
            "detection_types": detection_types(entry),
            "detection": describe_detection(entry),
            "start_time": start_time,
            # The same fact as on the entity: an automation triggering on the
            # bus should not have to go and read a switch to learn it.
            "snoozed": self.coordinator.snoozed,
        })
        self.async_write_ha_state()
