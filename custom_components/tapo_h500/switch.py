"""Hub settings that are simply on or off.

Every switch here was verified against an H500 on firmware 1.3.20 by writing
the hub's own current value back to it and confirming error_code 0 with the
setting unchanged. Settings whose setter could not be proven safe that way are
deliberately absent: setReboot (ambiguous between scheduling and rebooting),
setMediaEncrypt (would break the verified download path) and setTimezone
(would shift every clip timestamp).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_HUBS, DOMAIN
from .hub_control import H500HubControl
from .status import auto_upgrade_config, face_detection_config


@dataclass(frozen=True, kw_only=True)
class HubSwitch(SwitchEntityDescription):
    value: Callable[[dict], bool | None]
    apply: Callable[[object, dict, bool], object]


def _auto_upgrade(client, readings: dict, on: bool):
    """Toggle only `enabled`, keeping the schedule the hub already holds."""
    return client.set_auto_upgrade(auto_upgrade_config(readings, on))


def _face_detection(client, readings: dict, on: bool):
    """Toggle only `enabled`, sending the tag list back with it."""
    return client.set_face_detection(face_detection_config(readings, on))


HUB_SWITCHES: tuple[HubSwitch, ...] = (
    HubSwitch(
        key="led", translation_key="led",
        entity_category=EntityCategory.CONFIG,
        value=lambda r: r.get("led_on"),
        apply=lambda client, _r, on: client.set_led(on),
    ),
    HubSwitch(
        key="loop_recording", translation_key="loop_recording",
        entity_category=EntityCategory.CONFIG,
        value=lambda r: r.get("loop_recording"),
        apply=lambda client, _r, on: client.set_loop_recording(on),
    ),
    HubSwitch(
        key="auto_upgrade", translation_key="auto_upgrade",
        entity_category=EntityCategory.CONFIG,
        value=lambda r: r.get("auto_upgrade"),
        apply=_auto_upgrade,
    ),
    HubSwitch(
        key="face_detection", translation_key="face_detection",
        entity_category=EntityCategory.CONFIG,
        value=lambda r: r.get("face_detection"),
        apply=_face_detection,
    ),
    HubSwitch(
        key="diagnose_mode", translation_key="diagnose_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("diagnose_mode"),
        apply=lambda client, _r, on: client.set_diagnose_mode(on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    async_add_entities(
        H500HubSwitch(coordinator, entry, description)
        for description in HUB_SWITCHES
    )


class H500HubSwitch(H500HubControl, SwitchEntity):
    def __init__(self, coordinator, entry: ConfigEntry, description: HubSwitch) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.readings)

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        await self.apply(partial(
            self.entity_description.apply, self.coordinator.client,
            self.readings, on))
