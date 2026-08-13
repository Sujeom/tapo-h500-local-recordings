"""Hub and per-camera sensors, built only from responses seen on real hardware."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .clips import (
    busiest_hour, events_since, unique_faces, unknown_face_count,
)
from .const import DATA_HUBS, DOMAIN, SIGNAL_FACES_CHANGED
from .coordinator import H500Coordinator
from .entity import H500Entity


@dataclass(frozen=True, kw_only=True)
class HubSensor(SensorEntityDescription):
    value: Callable[[dict], object]


@dataclass(frozen=True, kw_only=True)
class CameraSensor(SensorEntityDescription):
    value: Callable[[H500Coordinator, int, dict], object]


HUB_SENSORS: tuple[HubSensor, ...] = (
    HubSensor(
        key="storage_free", translation_key="storage_free",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda r: r.get("storage_free_gb"),
    ),
    HubSensor(
        key="storage_total", translation_key="storage_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value=lambda r: r.get("storage_total_gb"),
    ),
    HubSensor(
        key="storage_used", translation_key="storage_used",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda r: r.get("storage_used_percent"),
    ),
    HubSensor(
        key="storage_status", translation_key="storage_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("storage_status"),
    ),
    HubSensor(
        key="siren_time_left", translation_key="siren_time_left",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="s",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("siren_time_left"),
    ),
    HubSensor(
        key="firmware_state", translation_key="firmware_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("firmware_state"),
    ),
    # Clip filenames and the media browser's date folders come from hub
    # timestamps, so drift here files recordings under the wrong day. Signed:
    # ahead and behind are different faults.
    HubSensor(
        key="clock_offset", translation_key="clock_offset",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("clock_offset"),
    ),
    HubSensor(
        key="timezone", translation_key="timezone",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("timezone"),
    ),
    # The hub holds five custom sound slots. Empty ones come back as empty
    # strings rather than being absent, so this counts named slots.
    HubSensor(
        key="custom_sounds", translation_key="custom_sounds",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda r: r.get("custom_sounds"),
    ),
    # Health, so a hub that is quietly struggling is visible before it stops.
    # Assembled from readings already collected rather than new calls.
    HubSensor(
        key="hub_health", translation_key="hub_health",
        value=lambda r: (
            "unreachable" if not r else
            "storage full" if (r.get("storage_used_percent") or 0) >= 99 else
            "storage failing" if r.get("storage_healthy") is False else
            "clock drifted" if abs(r.get("clock_offset") or 0) > 60 else
            "ok"),
    ),
    HubSensor(
        key="ip_address", translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("ip_address"),
    ),
)

CAMERA_SENSORS: tuple[CameraSensor, ...] = (
    CameraSensor(
        key="last_activity", translation_key="last_activity",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda c, i, cam: (
            dt_util.utc_from_timestamp(c.last_activity(i))
            if c.last_activity(i) is not None else None),
    ),
    CameraSensor(
        key="recordings_1h", translation_key="recordings_1h",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="recordings",
        value=lambda c, i, cam: events_since(
            c.clips_for(i), int(dt_util.utcnow().timestamp()) - 3600),
    ),
    CameraSensor(
        key="recordings_24h", translation_key="recordings_24h",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="recordings",
        value=lambda c, i, cam: len(c.clips_for(i)),
    ),
    # Statistics, all with a state class so the recorder keeps them for the
    # long-term graphs. They are computed from the polled window rather than
    # stored: the window is a day, and anything longer is the recorder's job.
    CameraSensor(
        key="busiest_hour", translation_key="busiest_hour",
        value=lambda c, i, cam: busiest_hour(c.clips_for(i)),
    ),
    CameraSensor(
        key="people_seen", translation_key="people_seen",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="people",
        value=lambda c, i, cam: unique_faces(c.clips_for(i)),
    ),
    CameraSensor(
        key="unknown_faces", translation_key="unknown_faces",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="recordings",
        value=lambda c, i, cam: unknown_face_count(c.clips_for(i)),
    ),
    CameraSensor(
        key="ai_enhance", translation_key="ai_enhance",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="%",
        value=lambda c, i, cam: cam.get("ai_enhance"),
    ),
    CameraSensor(
        key="network_mode", translation_key="network_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c, i, cam: cam.get("network_mode"),
    ),
    CameraSensor(
        key="model", translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c, i, cam: cam.get("device_model"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    entities: list[SensorEntity] = [
        H500HubSensor(coordinator, entry, description)
        for description in HUB_SENSORS
    ]
    entities += [
        H500CameraSensor(coordinator, index, camera, description)
        for index, camera in enumerate(coordinator.cameras)
        for description in CAMERA_SENSORS
    ]
    async_add_entities(entities)

    # One per named face, added as names appear rather than on a reload.
    # Naming used to reload the whole entry, which cost a hub login and broke
    # whatever was mid-request; now the entry is left alone and this listens
    # for the change instead.
    added: set[str] = set()

    @callback
    def _sync_faces() -> None:
        new_ids = [face_id for face_id in sorted(coordinator.face_names)
                   if face_id not in added]
        if not new_ids:
            return
        added.update(new_ids)
        # Two per person: when they were last seen, and where. The pair is
        # what makes following someone between cameras readable at a glance --
        # the hub gives one id per person across the whole house, so "where"
        # is a real answer rather than a guess.
        async_add_entities(
            [H500FaceSensor(coordinator, entry, face_id) for face_id in new_ids]
            + [H500FaceLocationSensor(coordinator, entry, face_id)
               for face_id in new_ids])

    _sync_faces()
    entry.async_on_unload(async_dispatcher_connect(
        hass, f"{SIGNAL_FACES_CHANGED}_{entry.entry_id}", _sync_faces))


class H500HubSensor(CoordinatorEntity[H500Coordinator], SensorEntity):
    """A reading about the hub itself rather than any one camera."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description: HubSensor) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def native_value(self):
        return self.entity_description.value(self.coordinator.readings)


class H500CameraSensor(H500Entity, SensorEntity):
    def __init__(self, coordinator, index, camera, description: CameraSensor) -> None:
        super().__init__(coordinator, index, camera)
        self.entity_description = description
        self._attr_unique_id = f"{camera['device_id']}_{description.key}"

    @property
    def native_value(self):
        return self.entity_description.value(
            self.coordinator, self.index, self.camera)


def hub_device(coordinator: H500Coordinator, entry: ConfigEntry):
    from homeassistant.helpers.device_registry import DeviceInfo

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="TP-Link",
        model="H500",
        configuration_url=f"https://{entry.data.get('host')}",
    )


class H500FaceSensor(CoordinatorEntity[H500Coordinator], SensorEntity):
    """When a named person was last seen, across every camera.

    One per name in the shared map rather than one per id the hub has ever
    emitted: the hub invents an id for every face it clusters, including
    passers-by, and an entity per stranger would fill the registry with
    numbered ghosts that never return. Naming someone is the signal that they
    are worth tracking.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry, face_id: str) -> None:
        super().__init__(coordinator)
        self.face_id = str(face_id)
        self._attr_unique_id = f"{entry.entry_id}_face_{self.face_id}"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def name(self) -> str:
        """Read live, so renaming someone takes effect without a reload.

        A name captured at construction would leave the entity showing the old
        one until the integration restarted -- and avoiding that restart is the
        point of this whole path.
        """
        return self.coordinator.face_names.get(self.face_id) or f"Face {self.face_id}"

    @property
    def _face(self) -> dict:
        return self.coordinator.faces_seen().get(self.face_id) or {}

    @property
    def native_value(self):
        seen = self._face.get("last_seen")
        return dt_util.utc_from_timestamp(seen) if seen else None

    @property
    def extra_state_attributes(self) -> dict:
        face = self._face
        return {
            "face_id": self.face_id,
            # Within the poll window only, which is what every other count in
            # this integration means; a lifetime total would need a database.
            "sightings": face.get("sightings", 0),
            "cameras": face.get("cameras", []),
        }


class H500FaceLocationSensor(CoordinatorEntity[H500Coordinator], SensorEntity):
    """Which camera last saw this person, and the trail of ones before it.

    Face ids are hub-wide rather than per-camera -- measured on this hardware,
    two of six ids appeared on both doorbells -- so the same number really does
    follow one person from door to door. This is that, surfaced.

    It reports where the hub last SAW someone, which is not where they are.
    Nobody is tracked between sightings and a quiet camera means nothing was
    detected, not that the person left; the state simply stops changing.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:map-marker-account"

    def __init__(self, coordinator, entry, face_id: str) -> None:
        super().__init__(coordinator)
        self.face_id = str(face_id)
        self._attr_unique_id = f"{entry.entry_id}_face_{self.face_id}_location"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def name(self) -> str:
        who = self.coordinator.face_names.get(self.face_id) \
            or f"Face {self.face_id}"
        return f"{who} last seen at"

    @property
    def _face(self) -> dict:
        return self.coordinator.faces_seen().get(self.face_id) or {}

    @property
    def native_value(self):
        # None rather than "unknown" or a stale camera: outside the polled
        # window there is genuinely no answer, and inventing one would read as
        # "they are at the front door" long after they left.
        return self._face.get("last_camera")

    @property
    def extra_state_attributes(self) -> dict:
        face = self._face
        trail = face.get("trail") or []
        return {
            "face_id": self.face_id,
            "cameras": face.get("cameras", []),
            "sightings": face.get("sightings", 0),
            # "approaching", "leaving", or absent when it is not known --
            # which is the usual case until cameras are given an order.
            "direction": face.get("direction"),
            # Newest first: camera and when, so a history of one person moving
            # between doors is readable without joining anything up by hand.
            "trail": [{"camera": hop["camera"],
                       "at": dt_util.utc_from_timestamp(hop["at"]).isoformat()}
                      for hop in trail],
        }
