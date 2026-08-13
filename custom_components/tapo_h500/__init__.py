"""Tapo H500 local recording integration."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryNotReady, HomeAssistantError, ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.loader import async_get_integration

from .api import H500Client
from .clips import (
    describe_detection, detection_types, end_of, event_type, face_ids,
    start_of,
)
from .const import (
    CARD_URL, CONF_CLOUD_PASSWORD, CONF_CONVERT_MP4, DATA_CARD, DATA_HUBS,
    DATA_PREVIEW, DEFAULT_CONVERT_MP4, DOMAIN, SERVICE_DELETE_RECORDING,
    SERVICE_DOWNLOAD_RECORDING, SERVICE_FORMAT_HUB_STORAGE,
    SERVICE_LIST_RECORDINGS,
    SERVICE_NAME_FACE,
    CONF_FACE_NAMES,
)
from .coordinator import H500Coordinator
from .media import (
    async_delete_clip, async_download_clip, describe, media_root, scan_downloaded,
)
from .preview import H500PreviewView, preview_url

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CAMERA, Platform.EVENT,
             Platform.IMAGE, Platform.NUMBER, Platform.SELECT, Platform.SENSOR,
             Platform.SIREN, Platform.SWITCH]

NONNEGATIVE_INT = vol.All(vol.Coerce(int), vol.Range(min=0))
ENTRY_SCHEMA = {
    vol.Required("config_entry_id"): cv.string,
    vol.Optional("camera_index", default=0): NONNEGATIVE_INT,
}
LIST_SCHEMA = vol.Schema({
    **ENTRY_SCHEMA,
    vol.Optional("start_date"): cv.string,
    vol.Optional("end_date"): cv.string,
})
DOWNLOAD_SCHEMA = vol.Schema({
    **ENTRY_SCHEMA,
    vol.Required("start_time"): NONNEGATIVE_INT,
    vol.Required("end_time"): NONNEGATIVE_INT,
    vol.Optional("convert_to_mp4"): cv.boolean,
})
DELETE_SCHEMA = vol.Schema({
    **ENTRY_SCHEMA,
    vol.Required("start_time"): NONNEGATIVE_INT,
})
NAME_FACE_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    # The hub reports ids as numbers; accept either spelling and store one.
    vol.Required("face_id"): vol.All(vol.Coerce(str), vol.Length(min=1)),
    # Omitted or empty clears the name rather than storing a blank one.
    vol.Optional("name", default=""): cv.string,
})
FORMAT_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("confirm"): vol.All(cv.boolean, vol.Equal(True)),
})

SERVICES = (
    SERVICE_LIST_RECORDINGS,
    SERVICE_NAME_FACE,
    CONF_FACE_NAMES, SERVICE_DOWNLOAD_RECORDING,
    SERVICE_DELETE_RECORDING, SERVICE_FORMAT_HUB_STORAGE,
)


def _public_camera(camera):
    return {
        "alias": camera.get("alias") or camera.get("device_name") or "Camera",
        "model": camera.get("device_model"),
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    media_root(hass)  # fails the entry early with a usable message
    client = H500Client(
        entry.data[CONF_HOST], entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD], entry.data[CONF_CLOUD_PASSWORD],
    )
    try:
        await hass.async_add_executor_job(client.connect)
    except Exception as err:
        await hass.async_add_executor_job(client.close)
        raise ConfigEntryNotReady(
            f"Cannot reach the H500 at {entry.data[CONF_HOST]}: {err}") from err

    coordinator = H500Coordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {}).setdefault(DATA_HUBS, {})[
        entry.entry_id] = coordinator

    await _async_register_card(hass)
    if not hass.data[DOMAIN].get(DATA_PREVIEW):
        hass.http.register_view(H500PreviewView())
        hass.data[DOMAIN][DATA_PREVIEW] = True
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not hass.services.has_service(DOMAIN, SERVICE_LIST_RECORDINGS):
        _register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
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
    hubs = hass.data.get(DOMAIN, {}).get(DATA_HUBS, {})
    coordinator = hubs.pop(entry.entry_id, None)
    if coordinator is not None:
        await hass.async_add_executor_job(coordinator.client.close)
    if not hubs:
        for service in SERVICES:
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    return True


def _coordinator(hass, entry_id) -> H500Coordinator:
    try:
        return hass.data[DOMAIN][DATA_HUBS][entry_id]
    except KeyError as err:
        raise ServiceValidationError(
            "Unknown or unloaded Tapo H500 config entry") from err


async def _resolve(hass, call: ServiceCall):
    """The coordinator and the selected camera for a service call."""
    coordinator = _coordinator(hass, call.data["config_entry_id"])
    try:
        camera = await hass.async_add_executor_job(
            coordinator.client.camera_at, call.data["camera_index"])
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except Exception as err:
        raise HomeAssistantError("Unable to list H500 cameras") from err
    return coordinator, camera


def _register_services(hass: HomeAssistant) -> None:
    async def list_recordings(call: ServiceCall):
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        try:
            camera, recordings = await hass.async_add_executor_job(
                coordinator.client.recordings, call.data["camera_index"],
                call.data.get("start_date"), call.data.get("end_date"))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except Exception as err:
            raise HomeAssistantError("Unable to list H500 recordings") from err

        clips = [
            (start_of(clip), end_of(clip), clip) for clip in recordings
        ]
        clips = [item for item in clips if item[0] is not None and item[1] is not None]
        on_disk = await hass.async_add_executor_job(
            scan_downloaded, hass, camera, [start for start, _, _ in clips])
        return {
            "camera": _public_camera(camera),
            # The shared name map, so a card shows names without being told
            # them. A card may still override it locally.
            "face_names": coordinator.face_names,
            # So a caller can offer a camera picker without probing indexes.
            "cameras": [
                {"index": position, **_public_camera(item)}
                for position, item in enumerate(coordinator.cameras)
            ],
            "recordings": [
                {
                    "start_time": start,
                    "end_time": end,
                    "duration": end - start,
                    "event_type": event_type(clip),
                    "video_type": clip.get("video_type"),
                    # What the hub says actually triggered it. video_type is
                    # "2" for everything, so this is the useful one.
                    "detection": describe_detection(clip),
                    "alarm_type": clip.get("alarm_type"),
                    "detection_types": detection_types(clip),
                    "face_ids": face_ids(clip),
                    "downloaded": start in on_disk,
                    # A clip still only on the hub gets a preview URL rather
                    # than nothing. It is generated when something actually
                    # asks for the image, not here, so listing stays one call.
                    **(describe(hass, on_disk[start]) if start in on_disk else {
                        "thumbnail": preview_url(
                            hass, call.data["config_entry_id"],
                            call.data["camera_index"], start),
                    }),
                }
                for start, end, clip in sorted(clips, key=lambda item: item[0])
            ],
        }

    async def download_recording(call: ServiceCall):
        coordinator, camera = await _resolve(hass, call)
        start_time = call.data["start_time"]
        end_time = call.data["end_time"]
        if end_time <= start_time:
            raise ServiceValidationError("end_time must be after start_time")
        convert = call.data.get(
            "convert_to_mp4",
            coordinator.entry.options.get(CONF_CONVERT_MP4, DEFAULT_CONVERT_MP4),
        )
        result = await async_download_clip(
            hass, coordinator.client, camera, start_time, end_time, convert)
        coordinator.async_update_listeners()
        return result

    async def delete_recording(call: ServiceCall):
        _, camera = await _resolve(hass, call)
        removed = await async_delete_clip(hass, camera, call.data["start_time"])
        if not removed:
            raise ServiceValidationError(
                "No downloaded copy of that recording was found")
        return {"removed": removed}

    async def format_hub_storage(call: ServiceCall):
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        _LOGGER.warning("Erasing all recordings on the H500 at %s",
                        coordinator.client.host)
        try:
            await hass.async_add_executor_job(coordinator.client.format_storage)
        except Exception as err:
            raise HomeAssistantError(
                f"The H500 refused to format its storage: {err}") from err
        return {"formatted": True}

    async def name_face(call: ServiceCall):
        """Give a hub face id a name, or clear it by passing none.

        Written to the config entry's options, which is what the per-face
        sensors and every card read, so one edit reaches all of them. Home
        Assistant reloads the entry on an options change, which is how a newly
        named face gains its sensor.
        """
        coordinator = _coordinator(hass, call.data["config_entry_id"])
        face_id = str(call.data["face_id"])
        name = (call.data.get("name") or "").strip()
        names = dict(coordinator.entry.options.get(CONF_FACE_NAMES) or {})
        if name:
            names[face_id] = name
        else:
            names.pop(face_id, None)
        hass.config_entries.async_update_entry(
            coordinator.entry,
            options={**coordinator.entry.options, CONF_FACE_NAMES: names})
        return {"face_id": face_id, "name": name or None,
                "named": sorted(names)}

    for service, handler, schema in (
        (SERVICE_LIST_RECORDINGS, list_recordings, LIST_SCHEMA),
        (SERVICE_DOWNLOAD_RECORDING, download_recording, DOWNLOAD_SCHEMA),
        (SERVICE_DELETE_RECORDING, delete_recording, DELETE_SCHEMA),
        (SERVICE_FORMAT_HUB_STORAGE, format_hub_storage, FORMAT_SCHEMA),
        (SERVICE_NAME_FACE, name_face, NAME_FACE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN, service, handler, schema=schema,
            supports_response=SupportsResponse.ONLY,
        )
