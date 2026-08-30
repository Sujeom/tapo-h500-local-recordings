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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_HUBS, DOMAIN
from .hub_control import H500HubControl
from .status import auto_upgrade_config, face_detection_config

# One at a time. Every write here is a call to a hub that wedges under
# concurrent sessions and recovers only on a timeout, so a scene touching
# four of these entities must not open four at once.
PARALLEL_UPDATES = 1


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
        [H500HubSwitch(coordinator, entry, description)
         for description in HUB_SWITCHES]
        + [H500Snooze(coordinator, entry)]
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


class H500Snooze(CoordinatorEntity, SwitchEntity):
    """Mute notifications without disabling the automation.

    The only way to stop the phone buzzing used to be turning the automation
    off, which is a thing people forget to turn back on. An afternoon of
    gardening, a party, a delivery you are waiting for at the door: all of
    them are worth an hour of quiet and none of them is worth a doorbell that
    stays silent for a week.

    Nothing here stops recording, downloading or firing events. Footage during
    a snooze is the footage most likely to be wanted afterwards. This is a
    flag for the automation to read, and the blueprint does.

    Not written to disk on purpose. A snooze that outlived a restart would be
    a silent doorbell nobody remembered turning off.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "snoozed"
    _attr_icon = "mdi:bell-sleep"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_snoozed"
        from .sensor import hub_device
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def is_on(self) -> bool:
        return self.coordinator.snoozed

    @property
    def extra_state_attributes(self) -> dict:
        until = self.coordinator.snoozed_until
        from homeassistant.util import dt as dt_util
        return {
            # None while off, and absent-as-"indefinitely" when the switch was
            # flipped by hand rather than given a duration.
            "until": (dt_util.utc_from_timestamp(until).isoformat()
                      if until not in (None, float("inf")) else None),
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Indefinitely. A duration comes from the snooze action instead --
        Home Assistant's switch.turn_on carries nowhere to put one."""
        self.coordinator.snooze(None)

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.snooze(0)
