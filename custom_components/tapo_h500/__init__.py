"""Tapo H500 local recording integration."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import (
    HomeAssistant, HomeAssistantError, ServiceCall, ServiceValidationError,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv

from .api import H500Client, safe_filename
from .const import (
    CONF_CLOUD_PASSWORD, DATA_CLIENTS, DOMAIN, SERVICE_DOWNLOAD_RECORDING,
    SERVICE_LIST_RECORDINGS,
)

NONNEGATIVE_INT = vol.All(vol.Coerce(int), vol.Range(min=0))
LIST_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Optional("camera_index", default=0): NONNEGATIVE_INT,
    vol.Optional("start_date"): cv.string,
    vol.Optional("end_date"): cv.string,
})
DOWNLOAD_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Optional("camera_index", default=0): NONNEGATIVE_INT,
    vol.Required("start_time"): NONNEGATIVE_INT,
    vol.Required("end_time"): NONNEGATIVE_INT,
})


def _public_camera(camera):
    return {
        "alias": camera.get("alias") or camera.get("device_name") or "Camera",
        "model": camera.get("device_model"),
    }


def _public_recording(recording):
    return {
        "start_time": int(recording["startTime"]),
        "end_time": int(recording["endTime"]),
        "video_type": recording.get("video_type"),
    }


def _make_temp(parent: Path) -> tuple[int, Path]:
    parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".tapo-h500-", suffix=".part", dir=parent)
    return descriptor, Path(name)


async def async_setup_entry(hass: HomeAssistant, entry):
    if "local" not in hass.config.media_dirs:
        raise HomeAssistantError(
            "Tapo H500 requires a Home Assistant media directory named 'local'")
    client = H500Client(
        entry.data[CONF_HOST], entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD], entry.data[CONF_CLOUD_PASSWORD],
    )
    await hass.async_add_executor_job(client.connect)
    hass.data.setdefault(DOMAIN, {}).setdefault(DATA_CLIENTS, {})[
        entry.entry_id] = client
    if not hass.services.has_service(DOMAIN, SERVICE_LIST_RECORDINGS):
        _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry):
    domain_data = hass.data.get(DOMAIN, {})
    clients = domain_data.get(DATA_CLIENTS, {})
    client = clients.pop(entry.entry_id, None)
    if client is not None:
        await hass.async_add_executor_job(client.close)
    if not clients:
        if hass.services.has_service(DOMAIN, SERVICE_LIST_RECORDINGS):
            hass.services.async_remove(DOMAIN, SERVICE_LIST_RECORDINGS)
        if hass.services.has_service(DOMAIN, SERVICE_DOWNLOAD_RECORDING):
            hass.services.async_remove(DOMAIN, SERVICE_DOWNLOAD_RECORDING)
        hass.data.pop(DOMAIN, None)
    return True


def _client(hass, entry_id):
    try:
        return hass.data[DOMAIN][DATA_CLIENTS][entry_id]
    except KeyError as err:
        raise ServiceValidationError(
            "Unknown or unloaded Tapo H500 config entry") from err


def _camera(cameras, index):
    if index >= len(cameras):
        raise ServiceValidationError(
            f"Camera index must be between 0 and {len(cameras) - 1}")
    return cameras[index]


def _register_services(hass):
    async def list_recordings(call: ServiceCall):
        client = _client(hass, call.data["config_entry_id"])
        try:
            camera, recordings = await hass.async_add_executor_job(
                client.recordings, call.data["camera_index"],
                call.data.get("start_date"), call.data.get("end_date"))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except Exception as err:
            raise HomeAssistantError("Unable to list H500 recordings") from err
        return {
            "camera": _public_camera(camera),
            "recordings": [_public_recording(item) for item in recordings],
        }

    async def download_recording(call: ServiceCall):
        client = _client(hass, call.data["config_entry_id"])
        try:
            cameras = await hass.async_add_executor_job(client.cameras)
        except Exception as err:
            raise HomeAssistantError("Unable to list H500 cameras") from err
        camera = _camera(cameras, call.data["camera_index"])
        start_time = call.data["start_time"]
        end_time = call.data["end_time"]
        if end_time <= start_time:
            raise ServiceValidationError("end_time must be after start_time")
        filename = safe_filename(
            camera.get("alias") or camera.get("device_name") or "Camera",
            start_time,
        )
        relative = Path("tapo_h500") / filename
        media_root = Path(hass.config.media_dirs["local"]).resolve()
        output = media_root / relative
        descriptor, temporary = await hass.async_add_executor_job(
            _make_temp, output.parent)
        stream = os.fdopen(descriptor, "wb")
        received = 0
        try:
            async for chunk in client.iter_recording(camera, start_time, end_time):
                received += len(chunk)
                await hass.async_add_executor_job(stream.write, chunk)
            if received == 0:
                raise HomeAssistantError("H500 returned no video data")
            await hass.async_add_executor_job(stream.close)
            stream = None
            await hass.async_add_executor_job(os.replace, temporary, output)
        except Exception as err:
            if isinstance(err, HomeAssistantError):
                raise
            raise HomeAssistantError("H500 recording download did not complete") from err
        finally:
            if stream is not None:
                await hass.async_add_executor_job(stream.close)
            await hass.async_add_executor_job(temporary.unlink, True)
        return {
            "media_content_id": (
                f"media-source://media_source/local/{relative.as_posix()}"),
            "path": relative.as_posix(),
            "bytes": received,
        }

    hass.services.async_register(
        DOMAIN, SERVICE_LIST_RECORDINGS, list_recordings,
        schema=LIST_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DOWNLOAD_RECORDING, download_recording,
        schema=DOWNLOAD_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
