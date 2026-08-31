"""Still image per paired camera, taken from that camera's newest clip.

The H500's live media protocol for hub-attached battery cameras is not part of
the verified path, so this deliberately serves stills rather than a stream.
"""
from __future__ import annotations

from .models import Camera

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback


from .coordinator import H500Coordinator
from .entity import add_cameras_as_they_appear, H500Entity

# One at a time. Asking for a picture can reach the coordinator's frame
# fetch, which opens a media session against a hub that wedges under
# concurrent ones -- so two dashboards showing the same camera must not ask
# twice at once.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    add_cameras_as_they_appear(
        coordinator, entry, async_add_entities,
        lambda index, camera: [H500Camera(coordinator, index, camera)])


class H500Camera(H500Entity, Camera):
    _attr_name = None

    def __init__(self, coordinator: H500Coordinator, index: int,
                 camera: Camera) -> None:
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
        # Through the coordinator, which knows what the newest indexed clip
        # is and fetches its frame if no download has written it yet. A plain
        # newest-on-disk scan here is how the notification's Camera button
        # showed the previous event to everyone who pressed it.
        return await self.coordinator.async_latest_frame(self.index, self.camera)
