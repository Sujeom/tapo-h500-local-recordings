"""Polls the hub, turns new activity into events, and downloads rings."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .clips import end_of, event_type, start_of
from .const import (
    AUTO_DOWNLOAD_ALL, AUTO_DOWNLOAD_RINGS, CONF_AUTO_DOWNLOAD, CONF_CONVERT_MP4,
    CONF_KEEP_DOWNLOADS, CONF_POLL_INTERVAL, DEFAULT_AUTO_DOWNLOAD,
    DEFAULT_CONVERT_MP4, DEFAULT_KEEP_DOWNLOADS,
    DEFAULT_POLL_INTERVAL, DOMAIN, EVENT_RING, LOOKBACK_SECONDS, SIGNAL_NEW_CLIP,
)
from .media import async_download_clip, async_prune, existing_clip
from .status import hub_readings

_LOGGER = logging.getLogger(__name__)


class H500Coordinator(DataUpdateCoordinator[dict[int, list[dict]]]):
    """One poller per hub. Cameras are addressed by their paired-list index."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry,
            update_interval=timedelta(seconds=entry.options.get(
                CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
        )
        self.entry = entry
        self.client = client
        self.cameras: list[dict] = []
        self.readings: dict = {}
        self._seen_events: dict[int, set[int]] = {}
        self._seen_clips: dict[int, set[int]] = {}
        self._primed = False

    def signal(self, name: str, index: int) -> str:
        return f"{SIGNAL_NEW_CLIP}_{name}_{self.entry.entry_id}_{index}"

    def clips_for(self, index: int) -> list[dict]:
        return (self.data or {}).get("clips", {}).get(index, [])

    def last_activity(self, index: int) -> int | None:
        moments = [start_of(clip) for clip in self.clips_for(index)]
        moments = [moment for moment in moments if moment is not None]
        return max(moments) if moments else None

    async def _async_update_data(self) -> dict:
        try:
            cameras = await self.hass.async_add_executor_job(self.client.cameras)
        except Exception as err:
            raise UpdateFailed(f"Could not list H500 cameras: {err}") from err
        self.cameras = cameras

        try:
            self.readings = hub_readings(
                await self.hass.async_add_executor_job(self.client.hub_status))
        except Exception as err:
            # Status is a bonus; never fail the whole poll over it.
            _LOGGER.debug("Hub status unavailable: %s", err)

        now = int(dt_util.utcnow().timestamp())
        window = now - LOOKBACK_SECONDS
        clips_by_camera: dict[int, list[dict]] = {}
        for index, camera in enumerate(cameras):
            try:
                clips = await self.hass.async_add_executor_job(
                    self.client.recent, camera, window, now + 60)
                detections = await self.hass.async_add_executor_job(
                    self.client.detections, camera, window, now + 60)
            except Exception as err:
                raise UpdateFailed(f"Could not poll H500 activity: {err}") from err
            clips_by_camera[index] = clips

            # The detection log lands before a clip is indexed, so prefer it as
            # the event source. Downloads always come from the clip index,
            # which is the only place exact clip boundaries exist.
            announce = detections if detections is not None else clips
            self._fire(index, announce, self._seen_events, window)
            self._download_new(index, camera, clips, window)
        self._primed = True
        return {"clips": clips_by_camera, "hub": self.readings}

    def _fresh(self, index, entries, seen_map, window) -> list[dict]:
        seen = seen_map.setdefault(index, set())
        fresh = []
        for entry in entries:
            moment = start_of(entry)
            if moment is None or moment in seen:
                continue
            seen.add(moment)
            fresh.append((moment, entry))
        # Forget anything the poll window can no longer return.
        seen_map[index] = {t for t in seen if t >= window - LOOKBACK_SECONDS}
        return [entry for _, entry in sorted(fresh)]

    def _fire(self, index, entries, seen_map, window) -> None:
        for entry in self._fresh(index, entries, seen_map, window):
            if not self._primed:
                continue
            async_dispatcher_send(
                self.hass, self.signal("event", index), event_type(entry), entry)

    def _download_new(self, index, camera, clips, window) -> None:
        mode = self.entry.options.get(CONF_AUTO_DOWNLOAD, DEFAULT_AUTO_DOWNLOAD)
        for clip in self._fresh(index, clips, self._seen_clips, window):
            if not self._primed or mode not in (AUTO_DOWNLOAD_ALL, AUTO_DOWNLOAD_RINGS):
                continue
            if mode == AUTO_DOWNLOAD_RINGS and event_type(clip) != EVENT_RING:
                continue
            self.entry.async_create_background_task(
                self.hass, self._download(index, camera, clip),
                f"{DOMAIN} download {start_of(clip)}",
            )

    async def _download(self, index, camera, clip) -> None:
        start_time = start_of(clip)
        end_time = end_of(clip)
        if start_time is None or end_time is None or end_time <= start_time:
            return
        if existing_clip(self.hass, camera, start_time) is not None:
            return
        try:
            result = await async_download_clip(
                self.hass, self.client, camera, start_time, end_time,
                convert=self.entry.options.get(
                    CONF_CONVERT_MP4, DEFAULT_CONVERT_MP4),
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Automatic download of clip %s failed: %s",
                            start_time, err)
            return
        _LOGGER.debug("Downloaded %s (%s bytes)", result["path"], result["bytes"])
        # Only automatic downloads are pruned. A manual download is a
        # deliberate choice and is left alone.
        keep = self.entry.options.get(CONF_KEEP_DOWNLOADS, DEFAULT_KEEP_DOWNLOADS)
        for removed in await async_prune(self.hass, camera, keep):
            _LOGGER.debug("Pruned %s to keep the newest %s", removed, keep)
        async_dispatcher_send(self.hass, self.signal("image", index))
