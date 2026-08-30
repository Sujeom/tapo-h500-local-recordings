"""Firmware update entity for the hub.

Fed by a LOCAL read of the hub's own cached upgrade_info block, a few
times a day. Nothing here commands the hub to contact TP-Link -- the whole
point of this integration is hub and cameras with no internet access, so
on a WAN-blocked hub the block stays empty and the entity truthfully shows
current, because no update is coming to an offline hub. Installed comes
from the same basic_info the device page shows.

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

# One at a time. Every write here is a call to a hub that wedges under
# concurrent sessions and recovers only on a timeout, so a scene touching
# four of these entities must not open four at once.
PARALLEL_UPDATES = 1


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
