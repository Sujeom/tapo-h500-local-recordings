"""Still image per paired camera, taken from that camera's newest clip.

The H500's live media protocol for hub-attached battery cameras is not part of
the verified path, so this deliberately serves stills rather than a stream.
"""
from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_HUBS, DOMAIN
from .entity import H500Entity
from .media import async_latest_image


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    async_add_entities(
        H500Camera(coordinator, index, camera)
        for index, camera in enumerate(coordinator.cameras)
    )


class H500Camera(H500Entity, Camera):
    _attr_name = None

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        H500Entity.__init__(self, coordinator, index, camera)
        Camera.__init__(self)
        self._attr_unique_id = f"{camera['device_id']}_camera"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.coordinator.signal("image", self.index),
            self._handle_new_image))

    @callback
    def _handle_new_image(self) -> None:
        self.async_write_ha_state()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return await async_latest_image(self.hass, self.camera)
