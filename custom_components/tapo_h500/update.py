"""Firmware update entity for the hub.

The hub can ask TP-Link's cloud whether newer firmware exists -- pytapo's
own two-request batch, verified on this hardware -- and Home Assistant has a
whole entity type for exactly this answer. Installed comes from the same
basic_info the device page shows; latest from the cloud check the
coordinator runs a few times a day.

No install-from-here: setFirmwareUpgrade is deliberately unprobed on a hub
that is easy to wedge, so the entity reports and the app upgrades. An
up-to-date hub answers the check with an EMPTY upgrade block, which is why
latest falls back to installed rather than to unknown.
"""
from __future__ import annotations

from homeassistant.components.update import UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_HUBS, DOMAIN
from .coordinator import H500Coordinator
from .sensor import hub_device


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    async_add_entities([H500FirmwareUpdate(coordinator, entry)])


class H500FirmwareUpdate(CoordinatorEntity[H500Coordinator], UpdateEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_firmware"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def installed_version(self) -> str | None:
        version = self.coordinator.client.info.get("sw_version")
        # "1.3.20 Build 20260605 rel.62028" -- the build tail is noise here.
        return str(version).split(" Build ")[0] if version else None

    @property
    def latest_version(self) -> str | None:
        version = (self.coordinator.firmware_info or {}).get("version")
        # An empty cloud answer means up to date, not unknown.
        return version or self.installed_version

    @property
    def extra_state_attributes(self) -> dict:
        # Whatever the cloud actually said, so a pending update whose field
        # names this integration has never seen is still visible somewhere.
        return {"upgrade_info": (self.coordinator.firmware_info or {})
                .get("raw", {})}
