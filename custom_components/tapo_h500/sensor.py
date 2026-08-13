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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DATA_HUBS, DOMAIN
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
        key="recordings_24h", translation_key="recordings_24h",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="recordings",
        value=lambda c, i, cam: len(c.clips_for(i)),
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
    # One per named face. Naming someone through the name_face service updates
    # the entry's options, Home Assistant reloads it, and their sensor appears.
    entities += [
        H500FaceSensor(coordinator, entry, face_id, name)
        for face_id, name in sorted(coordinator.face_names.items(),
                                    key=lambda pair: pair[1].lower())
    ]
    async_add_entities(entities)


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

    def __init__(self, coordinator, entry, face_id: str, name: str) -> None:
        super().__init__(coordinator)
        self._face_id = str(face_id)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_face_{self._face_id}"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def _face(self) -> dict:
        return self.coordinator.faces_seen().get(self._face_id) or {}

    @property
    def native_value(self):
        seen = self._face.get("last_seen")
        return dt_util.utc_from_timestamp(seen) if seen else None

    @property
    def extra_state_attributes(self) -> dict:
        face = self._face
        return {
            "face_id": self._face_id,
            # Within the poll window only, which is what every other count in
            # this integration means; a lifetime total would need a database.
            "sightings": face.get("sightings", 0),
            "cameras": face.get("cameras", []),
        }
