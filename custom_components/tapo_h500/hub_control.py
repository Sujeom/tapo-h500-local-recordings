"""Shared plumbing for the writable hub settings.

Every control platform does the same three things: run a blocking client call
off the event loop, turn a hub refusal into something Home Assistant can show,
and poll once afterwards so the new value is not guessed at. Keeping that here
means switch, select and number each hold only their own mapping.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import H500Coordinator
from .sensor import hub_device


class H500HubControl(CoordinatorEntity[H500Coordinator]):
    """A hub setting Home Assistant can change."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def readings(self) -> dict:
        return self.coordinator.readings

    async def apply(self, action) -> None:
        try:
            await self.hass.async_add_executor_job(action)
        except Exception as err:
            raise HomeAssistantError(f"The H500 refused the change: {err}") from err
        # One poll after the write, not one per call: this hub is easy to
        # overload and the readings all arrive in a single round trip anyway.
        await self.coordinator.async_request_refresh()
