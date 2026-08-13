"""Siren loudness and run time, as stored on the hub.

Both bounds are the hub's own: volume outside 1-10 is refused with -40209.
Duration is in seconds and defaults to 300 on this firmware; it is capped at an
hour here because the hub is the thing making the noise and there is no undo
button on a siren.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_HUBS, DOMAIN, SIREN_VOLUME_MAX, SIREN_VOLUME_MIN
from .hub_control import H500HubControl

SIREN_DURATION_MAX = 3600


@dataclass(frozen=True, kw_only=True)
class HubNumber(NumberEntityDescription):
    value: Callable[[dict], float | None]
    apply: Callable[[object, int], object]


HUB_NUMBERS: tuple[HubNumber, ...] = (
    HubNumber(
        key="siren_volume", translation_key="siren_volume",
        native_min_value=SIREN_VOLUME_MIN, native_max_value=SIREN_VOLUME_MAX,
        native_step=1, entity_category=EntityCategory.CONFIG,
        value=lambda r: r.get("siren_volume"),
        apply=lambda client, value: client.set_siren_config(volume=value),
    ),
    HubNumber(
        key="siren_duration", translation_key="siren_duration",
        native_min_value=1, native_max_value=SIREN_DURATION_MAX,
        native_step=1, native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.CONFIG,
        value=lambda r: r.get("siren_duration"),
        apply=lambda client, value: client.set_siren_config(duration=value),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    async_add_entities(
        H500HubNumber(coordinator, entry, description)
        for description in HUB_NUMBERS
    )


class H500HubNumber(H500HubControl, NumberEntity):
    def __init__(self, coordinator, entry: ConfigEntry, description: HubNumber) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self.readings)

    async def async_set_native_value(self, value: float) -> None:
        await self.apply(partial(
            self.entity_description.apply, self.coordinator.client, int(value)))
