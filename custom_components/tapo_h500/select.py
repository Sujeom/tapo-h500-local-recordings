"""The siren sound, choosable without sounding the siren.

The siren entity can take a tone as a turn-on argument, but that only helps if
you are sounding it right now. This holds the hub's stored choice, so the tone a
future doorbell press or automation uses can be set on its own.
"""
from __future__ import annotations

import logging
from functools import partial

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_HUBS, DOMAIN
from .hub_control import H500HubControl

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    try:
        tones = await hass.async_add_executor_job(coordinator.client.siren_tones)
    except Exception as err:
        _LOGGER.debug("Could not read the siren tone list: %s", err)
        return
    # Without the hub's own list there is nothing to choose between, and a free
    # text box would just produce -40209s.
    if tones:
        async_add_entities([H500SirenTone(coordinator, entry, tones)])


class H500SirenTone(H500HubControl, SelectEntity):
    _attr_translation_key = "siren_tone"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry, tones: list[str]) -> None:
        super().__init__(coordinator, entry, "siren_tone")
        self._attr_options = tones

    @property
    def current_option(self) -> str | None:
        tone = self.readings.get("siren_tone")
        # Home Assistant logs a warning for a value outside the option list.
        return tone if tone in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.apply(partial(
            self.coordinator.client.set_siren_config, tone=option))
