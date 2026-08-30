"""Tapo H500 local recording integration."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import services
from .api import H500AuthError, H500Client
from .const import (
    CARD_URL, CONF_CLOUD_PASSWORD, DATA_CARD, DATA_PREVIEW, DOMAIN,
    RELOAD_ON_CHANGE, SIGNAL_FACES_CHANGED,
)
from .coordinator import H500Coordinator
from .media import media_root
from .preview import H500PreviewView

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CALENDAR,
             Platform.CAMERA, Platform.EVENT, Platform.IMAGE, Platform.NUMBER,
             Platform.SELECT, Platform.SENSOR, Platform.SIREN, Platform.SWITCH,
             Platform.UPDATE]

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions once, whether or not a hub is loaded.

    An action that exists only while an entry is loaded breaks automation
    validation: if the hub is unreachable at startup the entry does not load,
    the actions do not exist, and every automation calling one fails with
    "action not found" -- which reads as a broken automation rather than an
    offline hub. Each handler resolves its own config entry when it is
    called, and says so plainly when there is none.
    """
    services.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    media_root(hass)  # fails the entry early with a usable message
    client = H500Client(
        entry.data[CONF_HOST], entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD], entry.data[CONF_CLOUD_PASSWORD],
    )
    try:
        await hass.async_add_executor_job(client.connect)
    except H500AuthError as err:
        # The one failure a retry cannot fix, and retrying is not free here:
        # each one is another login to a hub that wedges under repeated ones.
        # This asks for a new password instead of hammering the hub forever.
        await hass.async_add_executor_job(client.close)
        raise ConfigEntryAuthFailed(
            "The H500 refused the stored credentials") from err
    except Exception as err:
        # Everything else, the wedge included, still schedules a retry.
        await hass.async_add_executor_job(client.close)
        raise ConfigEntryNotReady(
            f"Cannot reach the H500 at {entry.data[CONF_HOST]}: {err}") from err

    coordinator = H500Coordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # The connect() above is a login. A failed first refresh raises
        # ConfigEntryNotReady and Home Assistant retries the whole of setup on
        # a backoff, so without this each retry adds another login that is
        # never closed -- to a hub that wedges under repeated ones and
        # recovers only on a timeout. The exception still leaves, because it
        # is what schedules the retry.
        await hass.async_add_executor_job(client.close)
        raise

    # The entry owns its coordinator. Home Assistant clears this when the
    # entry unloads, so nothing has to remember to.
    entry.runtime_data = coordinator

    await _async_register_card(hass)
    # Spoken questions, registered once per Home Assistant rather than per hub.
    try:
        from .intent import async_setup_intents
        await async_setup_intents(hass)
    except Exception as err:  # noqa: BLE001 - voice is a bonus, never fatal
        _LOGGER.debug("Could not register intents: %s", err)
    if not hass.data[DOMAIN].get(DATA_PREVIEW):
        hass.http.register_view(H500PreviewView())
        hass.data[DOMAIN][DATA_PREVIEW] = True
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # What the options looked like at setup, so the listener can tell a
    # connection-affecting change from a cosmetic one.
    coordinator.options_snapshot = _reload_snapshot(entry)
    entry.async_on_unload(entry.add_update_listener(_async_options_changed))
    return True


def _reload_snapshot(entry: ConfigEntry) -> dict:
    return {key: entry.options.get(key) for key in RELOAD_ON_CHANGE}


async def _async_options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when something about the connection actually changed.

    Naming a face writes to the entry's options like any other setting, and
    reloading for that was doing real harm: it tore down the coordinator while
    the card that asked for the name was still using it, which is the "cannot
    get data from the hub" the card reported, and it opened a fresh login to a
    hub that wedges under repeated authentication.
    """
    coordinator = getattr(entry, "runtime_data", None)
    current = _reload_snapshot(entry)
    if coordinator is not None and getattr(
            coordinator, "options_snapshot", None) == current:
        # Face names, and nothing the hub connection cares about. Entities read
        # the map live, so telling them to redraw is the whole update.
        coordinator.options_snapshot = current
        async_dispatcher_send(hass, f"{SIGNAL_FACES_CHANGED}_{entry.entry_id}")
        coordinator.async_update_listeners()
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the dashboard card so it needs no manual Lovelace resource."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(DATA_CARD):
        return
    await hass.http.async_register_static_paths([StaticPathConfig(
        CARD_URL, str(Path(__file__).parent / "www" / "tapo-h500-card.js"), True)])

    # The URL carries the version so a browser holding a cached copy fetches
    # the new one instead of silently keeping the old card.
    integration = await async_get_integration(hass, DOMAIN)
    versioned = f"{CARD_URL}?v={integration.version}"

    # Only one mechanism, or the file loads twice and the second define()
    # throws. The resource list is what dashboards actually read; the extra JS
    # URL is the fallback for when that is unavailable, such as YAML mode.
    if not await _async_register_lovelace_resource(hass, versioned):
        add_extra_js_url(hass, versioned)
    data[DATA_CARD] = True


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add the card to the dashboard's resource list.

    add_extra_js_url alone is not enough in practice: it only applies on a full
    frontend load, so the card reads as "Custom element doesn't exist" until the
    browser happens to reload everything. A real resource is what the dashboard
    consults. Storage-mode dashboards only — YAML mode owns its own resource
    list and must not be written to.
    """
    try:
        resources = hass.data["lovelace"].resources
        if getattr(resources, "loaded", True) is False:
            await resources.async_load()
            resources.loaded = True
        for item in resources.async_items():
            if str(item.get("url", "")).startswith(CARD_URL):
                if item["url"] != url:
                    await resources.async_update_item(item["id"], {"url": url})
                return True
        await resources.async_create_item({"res_type": "module", "url": url})
        return True
    except Exception as err:  # storage layout differs across versions
        _LOGGER.warning(
            "Could not register the dashboard card automatically (%s). Add it "
            "by hand under Settings > Dashboards > Resources as a JavaScript "
            "Module pointing at %s", err, url)
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None:
        await hass.async_add_executor_job(coordinator.client.close)
    # The actions are not removed. They belong to the integration rather than
    # to any one entry, and an automation calling one while the hub is down
    # should get "no hub is set up" rather than "action not found".
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> bool:
    """Whether Home Assistant may delete this device from the registry.

    Unpair a camera from the hub and its device stays in Home Assistant
    forever, with its twenty-odd entities, every one of them unavailable and
    every one still in every entity picker. Without this hook the delete
    button is not offered at all -- Home Assistant refuses on the integration's
    behalf, on the assumption that it would come straight back.

    A camera the hub still lists is exactly that case and is refused, with the
    same reasoning: it would be recreated on the next poll, and a delete button
    that undoes itself is worse than one that is missing. Anything else is
    gone from the hub and safe to let go, the hub's own device included -- if
    somebody deletes that, the config entry goes with it, which is what they
    asked for.
    """
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        # Unloaded, so there is nothing to contradict. The registry entry is
        # the user's to remove.
        return True
    paired = {camera.get("device_id") for camera in coordinator.cameras}
    return not any(identifier in paired
                   for domain, identifier in device.identifiers
                   if domain == DOMAIN)


