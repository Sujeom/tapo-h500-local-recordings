"""Hub and per-camera on/off state."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_HUBS, DOMAIN
from .coordinator import H500Coordinator
from .entity import H500Entity
from .sensor import hub_device


@dataclass(frozen=True, kw_only=True)
class HubFlag(BinarySensorEntityDescription):
    value: Callable[[dict], bool | None]


@dataclass(frozen=True, kw_only=True)
class CameraFlag(BinarySensorEntityDescription):
    value: Callable[[dict], bool | None]


# Only readings with no control of their own live here. The siren, LED and loop
# recording moved to siren/switch entities, which carry the same state and can
# also change it; keeping a read-only twin of each was two entities per fact.
# Media encryption stays because it is deliberately not writable — changing it
# would break the download path.
HUB_FLAGS: tuple[HubFlag, ...] = (
    HubFlag(
        key="storage_problem", translation_key="storage_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # Inverted deliberately: PROBLEM is on when unhealthy.
        value=lambda r: None if r.get("storage_healthy") is None
        else not r["storage_healthy"],
    ),
    HubFlag(
        key="media_encrypted", translation_key="media_encrypted",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("media_encrypted"),
    ),
    # Read-only for the same reason as media encryption: the getter answers but
    # setFaceDetectionConfig refuses even a write of the hub's own value.
    HubFlag(
        key="face_detection", translation_key="face_detection",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("face_detection"),
    ),
)

CAMERA_FLAGS: tuple[CameraFlag, ...] = (
    CameraFlag(
        key="hub_storage", translation_key="hub_storage",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda cam: cam.get("hub_storage_enabled"),
    ),
    CameraFlag(
        key="continuous_recording", translation_key="continuous_recording",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda cam: cam.get("plan_24h_record"),
    ),
    CameraFlag(
        key="ai_enhance_enabled", translation_key="ai_enhance_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda cam: cam.get("AI_enhance_enabled"),
    ),
    CameraFlag(
        key="wifi_backup", translation_key="wifi_backup",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda cam: cam.get("wifi_backup_enabled"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        H500HubFlag(coordinator, entry, description) for description in HUB_FLAGS
    ]
    entities += [
        H500CameraFlag(coordinator, index, camera, description)
        for index, camera in enumerate(coordinator.cameras)
        for description in CAMERA_FLAGS
    ]
    async_add_entities(entities)


class H500HubFlag(CoordinatorEntity[H500Coordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description: HubFlag) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.coordinator.readings)


class H500CameraFlag(H500Entity, BinarySensorEntity):
    def __init__(self, coordinator, index, camera, description: CameraFlag) -> None:
        super().__init__(coordinator, index, camera)
        self.entity_description = description
        self._attr_unique_id = f"{camera['device_id']}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        # The paired-device record is refreshed on every poll.
        current = self.coordinator.cameras[self.index] \
            if self.index < len(self.coordinator.cameras) else self.camera
        return self.entity_description.value(current)
