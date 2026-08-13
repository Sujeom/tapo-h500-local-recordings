"""Doorbell and motion events for each paired camera."""
from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .clips import (
    describe_detection, detection_types, end_of, face_ids, hub_label,
    start_of,
)
from .const import DATA_HUBS, DOMAIN, EVENT_TYPES
from .entity import H500Entity
from .media import clip_path, signed_url


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    async_add_entities(
        H500ActivityEvent(coordinator, index, camera)
        for index, camera in enumerate(coordinator.cameras)
    )


class H500ActivityEvent(H500Entity, EventEntity):
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "activity"

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_activity"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.coordinator.signal("event", self.index), self._handle))

    def _own_frame(self, start_time: int | None) -> str | None:
        """Signed URL for the thumbnail of the clip this event refers to."""
        if start_time is None:
            return None
        try:
            return signed_url(
                self.hass, clip_path(self.hass, self.camera, start_time, ".jpg"))
        except Exception:  # noqa: BLE001 - an attribute is never worth failing on
            return None

    @callback
    def _handle(self, kind: str, entry: dict) -> None:
        start_time = start_of(entry)
        end_time = end_of(entry)
        self._trigger_event(kind, {
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
            "image": self._own_frame(start_time),
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
        })
        self.async_write_ha_state()
