"""One press to restart the hub.

The recovery for every failure this hardware has shown -- the media wedge,
and possibly the cameras going dark -- has been "reboot the hub", which
until now meant the Tapo app or the plug. A restart button on the device
page is the Home Assistant way to say the same thing, and it is what the
repair notices can point at.

Deliberately the only path to the verb: nothing in this integration reboots
the hub on its own, ever. A person presses, the hub restarts, roughly two
minutes of downtime, recordings intact -- the same thing the hub does to
itself nightly on its own schedule.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_HUBS, DOMAIN
from .coordinator import H500Coordinator
from .sensor import hub_device

# One at a time. Every write here is a call to a hub that wedges under
# concurrent sessions and recovers only on a timeout, so a scene touching
# four of these entities must not open four at once.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    async_add_entities([H500RestartButton(coordinator, entry)])


class H500RestartButton(CoordinatorEntity[H500Coordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_restart"
        self._attr_device_info = hub_device(coordinator, entry)

    async def async_press(self) -> None:
        """Ask the hub to restart.

        Two failure shapes mean opposite things here. A protocol refusal
        (an -40xxx code in the reply) is the hub saying no, and the person
        who pressed must hear it. The connection dying mid-acknowledgement
        is what SUCCESS looks like when the device being asked is the
        device carrying the answer -- the poll will fail for a couple of
        minutes and recover on its own backoff, exactly as it does through
        the hub's own nightly reboot.
        """
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.reboot)
        except Exception as err:  # noqa: BLE001 - the shapes differ, see above
            if "-40" in str(err):
                raise HomeAssistantError(
                    f"The hub refused the restart: {err}") from err
            _LOGGER.info(
                "Hub restart requested; the connection dropped while it "
                "acknowledged, which is what a reboot looks like (%s)",
                type(err).__name__)
        else:
            _LOGGER.info("Hub restart acknowledged; expect about two "
                         "minutes of downtime")
