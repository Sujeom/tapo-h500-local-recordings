"""Shared device identity, and adding entities for a camera the hub reports after setup."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import H500Coordinator
from .models import Camera

if TYPE_CHECKING:
    # Only ever an annotation. Importing it for real would pull in a Home
    # Assistant module this package does not otherwise need.
    from homeassistant.helpers.entity import Entity


def camera_name(camera: Camera, index: int) -> str:
    return camera.get("alias") or camera.get("device_name") or f"Camera {index}"


class H500Entity(CoordinatorEntity[H500Coordinator]):
    """One paired camera, addressed by its index in the hub's paired list."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: H500Coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator)
        self.index = index
        self.camera = camera
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, camera["device_id"])},
            name=camera_name(camera, index),
            manufacturer="TP-Link",
            model=camera.get("device_model"),
            # These cameras reach Home Assistant only through the hub -- no
            # IP of their own, no Wi-Fi, a sub-GHz radio link and nothing
            # else. Without this they sat in the device list as peers of it,
            # so nothing said that unplugging the hub takes all of them with
            # it, and the hub's page did not list what depends on it. It is
            # the hub's own identifier, which is what makes them nest under it.
            via_device=(DOMAIN, coordinator.entry.entry_id),
        )


def add_cameras_as_they_appear(
    coordinator: H500Coordinator,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[int, Camera], list[Entity]],
) -> None:
    """Build entities for cameras the hub reports later, not only now.

    The paired list is refreshed on a schedule, so a doorbell paired after
    setup is known to the coordinator within minutes -- and without this,
    nothing ever builds its entities. Reloading the entry is the only other
    route, it is not a thing anybody discovers on their own, and it costs a
    fresh login to a hub that dislikes them.

    `build(index, camera)` returns the entities for one camera. Indices
    already served are remembered, because the listener fires on every poll
    and adding the same entity twice is how a registry fills with hundreds of
    them.
    """
    served: set[int] = set()

    @callback
    def _sync() -> None:
        fresh = [(index, camera)
                 for index, camera in enumerate(coordinator.cameras)
                 if index not in served]
        if not fresh:
            return
        served.update(index for index, _ in fresh)
        async_add_entities(
            entity for index, camera in fresh for entity in build(index, camera))

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
