"""Thumbnails for clips that have not been downloaded, made on demand.

Generating a preview for every listed clip up front would open one media
session per clip against a hub that is easy to overload. Serving them from a
URL instead means the browser only asks for the rows it actually shows — the
card already marks its images ``loading="lazy"`` — and each answer is cached on
disk, so the cost is paid once per clip and never again.
"""
from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant

from .const import DATA_HUBS, DOMAIN
from .media import URL_LIFETIME, async_preview_clip

try:  # KEY_HASS replaced the string key; the fallback keeps older cores working
    from homeassistant.components.http import KEY_HASS
except ImportError:  # pragma: no cover
    KEY_HASS = "hass"

_LOGGER = logging.getLogger(__name__)

PREVIEW_PATH = "/api/tapo_h500/preview/{entry_id}/{camera_index}/{start_time}"


def preview_url(hass: HomeAssistant, entry_id: str, camera_index: int,
                start_time: int) -> str:
    """A signed URL the dashboard can put straight into <img>."""
    return async_sign_path(
        hass,
        PREVIEW_PATH.format(entry_id=entry_id, camera_index=camera_index,
                            start_time=int(start_time)),
        URL_LIFETIME,
    )


class H500PreviewView(HomeAssistantView):
    """Fetch a couple of seconds of one clip and return a single frame."""

    url = PREVIEW_PATH
    name = "api:tapo_h500:preview"

    async def get(self, request: web.Request, entry_id: str, camera_index: str,
                  start_time: str) -> web.Response:
        hass = request.app[KEY_HASS]
        try:
            index, start = int(camera_index), int(start_time)
        except ValueError:
            return web.Response(status=400, text="Bad preview request")
        if start < 0 or index < 0:
            return web.Response(status=400, text="Bad preview request")

        coordinator = hass.data.get(DOMAIN, {}).get(DATA_HUBS, {}).get(entry_id)
        if coordinator is None:
            return web.Response(status=404, text="Unknown config entry")
        try:
            camera = await hass.async_add_executor_job(
                coordinator.client.camera_at, index)
        except Exception:
            return web.Response(status=404, text="Unknown camera")

        try:
            path = await async_preview_clip(
                hass, coordinator.client, camera, start)
        except Exception as err:  # noqa: BLE001 - a preview must not 500
            # async_preview_clip catches its own download failures, but the
            # lines that set the session up -- building the path, making the
            # temp file -- run above that guard, and a start_time outside the
            # hub's retention reaches them. Unhandled, this view answers a
            # dashboard tile with a stack trace.
            _LOGGER.debug("Preview for clip %s failed: %s", start, err)
            path = None
        if path is None:
            # The card renders a missing image as a blank tile, which is the
            # right outcome for a clip the hub would not preview.
            return web.Response(status=404, text="No preview available")
        try:
            image = await hass.async_add_executor_job(path.read_bytes)
        except OSError as err:
            # Retention runs on its own schedule and does not know a request
            # is in flight, so the frame can be pruned between being made and
            # being read.
            _LOGGER.debug("Preview %s vanished before it was read: %s",
                          path, err)
            return web.Response(status=404, text="No preview available")
        return web.Response(
            body=image, content_type="image/jpeg",
            # Immutable: a clip's opening frame never changes, and the signed
            # URL already carries its own expiry.
            headers={"Cache-Control": "private, max-age=3600"},
        )
