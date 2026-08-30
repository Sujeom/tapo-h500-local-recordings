"""Shared device identity for the hub's paired cameras."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import H500Coordinator


def camera_name(camera, index: int) -> str:
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
