"""The hub siren, which is a real controllable device on this firmware.

Verified against an H500 on firmware 1.3.20: getSirenStatus, getSirenConfig and
getSirenTypeList all answer, and setSirenStatus/setSirenConfig are accepted with
error_code 0. Volume is 1-10 — the hub rejects 0 and 11 with -40209 — so Home
Assistant's 0.0-1.0 level is scaled onto that range.
"""
from __future__ import annotations

import logging
from functools import partial

from homeassistant.components.siren import (
    ATTR_DURATION, ATTR_TONE, ATTR_VOLUME_LEVEL, SirenEntity, SirenEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_HUBS, DOMAIN
from .coordinator import H500Coordinator
from .sensor import hub_device
from .status import hub_volume

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    try:
        tones = await hass.async_add_executor_job(coordinator.client.siren_tones)
    except Exception as err:
        # A siren that cannot list its sounds still turns on and off.
        _LOGGER.debug("Could not read the siren tone list: %s", err)
        tones = []
    async_add_entities([H500Siren(coordinator, entry, tones)])


class H500Siren(CoordinatorEntity[H500Coordinator], SirenEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "siren"

    def __init__(self, coordinator, entry: ConfigEntry, tones: list[str]) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_siren"
        self._attr_device_info = hub_device(coordinator, entry)
        features = (SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF
                    | SirenEntityFeature.VOLUME_SET | SirenEntityFeature.DURATION)
        if tones:
            features |= SirenEntityFeature.TONES
            self._attr_available_tones = tones
        self._attr_supported_features = features

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.readings.get("siren_active")

    @property
    def extra_state_attributes(self) -> dict:
        readings = self.coordinator.readings
        return {
            "tone": readings.get("siren_tone"),
            "volume": readings.get("siren_volume"),
            "duration": readings.get("siren_duration"),
            "time_left": readings.get("siren_time_left"),
        }

    async def async_turn_on(self, **kwargs) -> None:
        tone = kwargs.get(ATTR_TONE)
        level = kwargs.get(ATTR_VOLUME_LEVEL)
        duration = kwargs.get(ATTR_DURATION)
        # Config is a separate call, so apply it before sounding rather than
        # after, or the first seconds play with the previous settings.
        if tone is not None or level is not None or duration is not None:
            await self._call(partial(
                self.coordinator.client.set_siren_config, tone=tone,
                volume=None if level is None else hub_volume(level),
                duration=duration))
        await self._call(partial(self.coordinator.client.set_siren, True))
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._call(partial(self.coordinator.client.set_siren, False))
        await self.coordinator.async_request_refresh()

    async def _call(self, action) -> None:
        # Deliberately does not refresh: turning on can be two calls, and this
        # hub is easy to overload, so the caller polls once at the end instead.
        try:
            await self.hass.async_add_executor_job(action)
        except Exception as err:
            raise HomeAssistantError(f"The H500 refused the siren call: {err}") from err
