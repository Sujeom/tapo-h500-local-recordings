"""Device triggers, one per detection the hub can report.

Without these, automating "a person at the side door" means hand-writing a
template condition against `detection_types`. The codes were identified the
hard way; this is what turns that work into something pickable from the UI:

    Front Doorbell  ->  When a person is detected

Every named code in DETECTION_NAMES becomes a trigger. `detection_types` lists
everything that fired at once, so a person who also set off plain motion
matches the person trigger as well as the motion one -- which is what someone
building an automation expects, and is why this filters the list rather than
comparing against the single headline `alarm_type`.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import state as state_trigger
from homeassistant.const import (
    CONF_DEVICE_ID, CONF_DOMAIN, CONF_ENTITY_ID, CONF_PLATFORM, CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DETECTION_NAMES, DOMAIN

# "unknown face" -> "unknown_face". The slug is what ends up in the stored
# automation, so it is derived from the code's name rather than its number:
# a YAML automation reading `type: person` survives being read by a human.
TRIGGER_TYPES: dict[str, int] = {
    name.replace(" ", "_"): code for code, name in DETECTION_NAMES.items()
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend({
    vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    vol.Required(CONF_ENTITY_ID): str,
})


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Every detection this camera could report, offered as a trigger."""
    registry = er.async_get(hass)
    triggers: list[dict[str, str]] = []
    for entry in er.async_entries_for_device(registry, device_id):
        # The event entity is the one that carries detections. The hub device
        # has no event entity, so it contributes no triggers, which is right.
        if entry.domain != "event":
            continue
        triggers.extend({
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_ENTITY_ID: entry.id,
            CONF_TYPE: slug,
        } for slug in TRIGGER_TYPES)
    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Fire only when this detection is among the ones that fired."""
    code = TRIGGER_TYPES[config[CONF_TYPE]]
    # Device triggers store the registry id; older ones stored the entity id.
    # Accept either, so an automation written against an earlier version keeps
    # working rather than silently never firing.
    registry = er.async_get(hass)
    entity_id = er.async_validate_entity_id(registry, config[CONF_ENTITY_ID])

    @callback
    def _detected(run_variables, context=None):
        state = (run_variables.get("trigger") or {}).get("to_state")
        if state is None:
            return
        if code not in (state.attributes.get("detection_types") or []):
            return
        action(run_variables, context)

    state_config = await state_trigger.async_validate_trigger_config(
        hass, {
            state_trigger.CONF_PLATFORM: "state",
            CONF_ENTITY_ID: entity_id,
        })
    return await state_trigger.async_attach_trigger(
        hass, state_config, _detected, trigger_info, platform_type="device")
