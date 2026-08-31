"""Device triggers: everything this integration works out, pickable from the UI.

Three kinds, because the things worth triggering on arrive three ways.

**Detections.** One per named code in DETECTION_NAMES, off the camera's event
entity. Without these, automating "a person at the side door" means
hand-writing a template condition against `detection_types`; the codes were
identified the hard way and this is what turns that work into

    Front Doorbell  ->  When a person is detected

`detection_types` lists everything that fired at once, so a person who also set
off plain motion matches the person trigger as well as the motion one -- which
is what someone building an automation expects, and is why this filters the
list rather than comparing against the single headline `alarm_type`.

**Worked-out states.** Loitering, a possible delivery, somebody going round the
house. Each is a binary sensor that is already correct; the trigger is just its
turning on, offered where people look for it.

**Bus events.** Arriving home and a visit beginning. These carry data no entity
state can -- who, which cameras, what the hub saw -- and they are the two that
fire once per person rather than once per recording, which is the whole reason
they exist. They belong to the hub rather than to a camera, and a visit
genuinely can span two cameras.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import Context

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.components.homeassistant.triggers import state as state_trigger
from homeassistant.const import (
    CONF_DEVICE_ID, CONF_DOMAIN, CONF_ENTITY_ID, CONF_PLATFORM, CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .coordinator import loaded_hubs
from .const import (
    DETECTION_NAMES, DOMAIN, EVENT_ARRIVAL, EVENT_VISIT,
)

# "unknown face" -> "unknown_face". The slug is what ends up in the stored
# automation, so it is derived from the code's name rather than its number:
# a YAML automation reading `type: person` survives being read by a human.
TRIGGER_TYPES: dict[str, int] = {
    name.replace(" ", "_"): code for code, name in DETECTION_NAMES.items()
}

# Slug -> the tail of the binary sensor's unique id. Matched on the unique id
# rather than on the entity id, which the owner can rename to anything.
STATE_TRIGGERS: dict[str, str] = {
    "loitering": "_loitering",
    "possible_delivery": "_possible_delivery",
    "prowling": "_prowling",
}

# Slug -> the event on the bus. Hub-level: an arrival is a person rather than a
# camera, and a visit can now span two cameras.
EVENT_TRIGGERS: dict[str, str] = {
    "arrival": EVENT_ARRIVAL,
    "visit": EVENT_VISIT,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend({
    vol.Required(CONF_TYPE): vol.In(
        list(TRIGGER_TYPES) + list(STATE_TRIGGERS) + list(EVENT_TRIGGERS)),
    # Absent for the bus events, which have no entity behind them.
    vol.Optional(CONF_ENTITY_ID): str,
})


def _hub_entry_id(hass: HomeAssistant, device_id: str) -> str | None:
    """The config entry this device IS, when the device is a hub.

    Hub and camera devices are both identified as (DOMAIN, <something>) -- the
    entry id for one, the hub's own device id for the other -- so the shape
    cannot tell them apart. Being a loaded hub can.
    """
    device = dr.async_get(hass).async_get(device_id)
    hubs = {hub.entry.entry_id: hub for hub in loaded_hubs(hass)}
    for domain, identifier in (device.identifiers if device else ()):
        if domain == DOMAIN and identifier in hubs:
            return identifier
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Everything this device could trigger on."""
    registry = er.async_get(hass)
    base = {CONF_PLATFORM: "device", CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN}
    triggers: list[dict[str, str]] = []
    for entry in er.async_entries_for_device(registry, device_id):
        # The event entity is the one that carries detections. The hub has no
        # event entity, so it contributes no detection triggers, which is right.
        if entry.domain == "event":
            triggers.extend({**base, CONF_ENTITY_ID: entry.id, CONF_TYPE: slug}
                            for slug in TRIGGER_TYPES)
        if entry.domain != "binary_sensor":
            continue
        unique_id = entry.unique_id or ""
        triggers.extend(
            {**base, CONF_ENTITY_ID: entry.id, CONF_TYPE: slug}
            for slug, tail in STATE_TRIGGERS.items()
            if unique_id.endswith(tail))
    if _hub_entry_id(hass, device_id):
        triggers.extend({**base, CONF_TYPE: slug} for slug in EVENT_TRIGGERS)
    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    kind = config[CONF_TYPE]
    if kind in EVENT_TRIGGERS:
        return await _attach_event(hass, config, action, trigger_info)
    if kind in STATE_TRIGGERS:
        return await _attach_state(hass, config, action, trigger_info)
    return await _attach_detection(hass, config, action, trigger_info)


def _entity_id(hass: HomeAssistant, config: ConfigType) -> str:
    """Device triggers store the registry id; older ones stored the entity id.

    Accept either, so an automation written against an earlier version keeps
    working rather than silently never firing.
    """
    return er.async_validate_entity_id(er.async_get(hass), config[CONF_ENTITY_ID])


async def _attach_detection(
    hass: HomeAssistant, config: ConfigType, action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Fire only when this detection is among the ones that fired."""
    code = TRIGGER_TYPES[config[CONF_TYPE]]
    entity_id = _entity_id(hass, config)

    @callback
    def _detected(run_variables: dict[str, Any],
                  context: Context | None = None) -> None:
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


async def _attach_state(
    hass: HomeAssistant, config: ConfigType, action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Fire when the sensor turns on, and only then.

    `to: "on"` rather than any change: these all clear by themselves, and an
    automation firing again as somebody walks away is the sort of thing that
    gets the whole integration muted.
    """
    state_config = await state_trigger.async_validate_trigger_config(
        hass, {
            state_trigger.CONF_PLATFORM: "state",
            CONF_ENTITY_ID: _entity_id(hass, config),
            "to": "on",
        })
    return await state_trigger.async_attach_trigger(
        hass, state_config, action, trigger_info, platform_type="device")


async def _attach_event(
    hass: HomeAssistant, config: ConfigType, action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Fire on a bus event from THIS hub.

    Filtered on entry_id, because both events are fired by every hub and an
    unfiltered trigger on a two-hub installation announces the neighbours'
    front door as well.
    """
    event_config = event_trigger.TRIGGER_SCHEMA({
        CONF_PLATFORM: "event",
        "event_type": EVENT_TRIGGERS[config[CONF_TYPE]],
        "event_data": {"entry_id": _hub_entry_id(hass, config[CONF_DEVICE_ID])},
    })
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device")
