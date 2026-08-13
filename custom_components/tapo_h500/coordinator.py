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

from .clips import (
    attach_detections, detection_types, end_of, event_type, face_ids,
    start_of,
)
from .const import (
    AUTO_DOWNLOAD_ALL, AUTO_DOWNLOAD_RINGS, CONF_AUTO_DOWNLOAD, CONF_CONVERT_MP4,
    CONF_KEEP_DOWNLOADS, CONF_POLL_INTERVAL, DEFAULT_AUTO_DOWNLOAD,
    DEFAULT_CONVERT_MP4, DEFAULT_KEEP_DOWNLOADS,
    CAMERAS_MAX_AGE, CONF_FACE_NAMES, CONF_KEEP_RINGS, DEFAULT_KEEP_RINGS, DEFAULT_POLL_INTERVAL, DOMAIN, EVENT_RING,
    LOOKBACK_SECONDS, SIGNAL_NEW_CLIP, STATUS_MAX_AGE,
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
        self._polls = 0
        # Turn "refresh this every N seconds" into a poll count once, here, so
        # a longer interval configured in options does not silently turn into
        # a much longer refresh age.
        interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._status_every = max(1, round(STATUS_MAX_AGE / interval))
        self._cameras_every = max(1, round(CAMERAS_MAX_AGE / interval))

    def signal(self, name: str, index: int) -> str:
        return f"{SIGNAL_NEW_CLIP}_{name}_{self.entry.entry_id}_{index}"

    @property
    def face_names(self) -> dict[str, str]:
        """Owner-supplied names, keyed by the id the hub invented.

        Keys are normalised to strings because the hub reports ids as numbers
        while YAML and the services API hand them over as either, and a map
        that answers to 123 but not "123" looks empty for no visible reason.
        """
        stored = self.entry.options.get(CONF_FACE_NAMES) or {}
        return {str(key): str(value) for key, value in stored.items()}

    def faces_seen(self, index: int | None = None) -> dict[str, dict]:
        """Every face in the current window, newest sighting first.

        Mirrors the cards' own grouping so a sensor and a card never disagree
        about how many times someone has been seen.
        """
        indexes = range(len(self.cameras)) if index is None else [index]
        faces: dict[str, dict] = {}
        for position in indexes:
            for clip in self.clips_for(position):
                for face_id in face_ids(clip):
                    key = str(face_id)
                    seen = faces.setdefault(
                        key, {"id": key, "sightings": 0, "last_seen": None,
                              "camera_index": None, "cameras": set()})
                    seen["sightings"] += 1
                    moment = start_of(clip)
                    if moment is not None and (seen["last_seen"] is None
                                               or moment > seen["last_seen"]):
                        seen["last_seen"] = moment
                        # Which camera saw them last, so a caller can find the
                        # thumbnail for that sighting. A face id is a
                        # twelve-digit number and means nothing on its own;
                        # the picture is the only way to know who it is.
                        seen["camera_index"] = position
                    alias = self.cameras[position].get("alias")
                    if alias:
                        seen["cameras"].add(alias)
        names = self.face_names
        for key, face in faces.items():
            face["name"] = names.get(key)
            face["cameras"] = sorted(face["cameras"])
        return faces

    def clips_for(self, index: int) -> list[dict]:
        return (self.data or {}).get("clips", {}).get(index, [])

    def last_activity(self, index: int) -> int | None:
        moments = [start_of(clip) for clip in self.clips_for(index)]
        moments = [moment for moment in moments if moment is not None]
        return max(moments) if moments else None

    async def _async_update_data(self) -> dict:
        # The paired camera list changes only when a camera is added or
        # removed, and costs 58ms -- more than the detection lookups it used to
        # precede. Fetch it on the first poll and then rarely, but never leave
        # it empty: a failure with nothing cached is still fatal to the poll.
        if not self.cameras or self._polls % self._cameras_every == 0:
            try:
                self.cameras = await self.hass.async_add_executor_job(
                    self.client.cameras)
            except Exception as err:
                if not self.cameras:
                    raise UpdateFailed(
                        f"Could not list H500 cameras: {err}") from err
                # A refresh failing is survivable; the cached list is still good.
                _LOGGER.debug("Camera list refresh failed, keeping cached: %s", err)
        cameras = self.cameras

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
            # One record per clip carrying both what was recorded and what
            # triggered it, so every consumer classifies the same way.
            attach_detections(clips, detections)
            clips_by_camera[index] = clips

            # The detection log lands before a clip is indexed, so prefer it as
            # the event source. Downloads always come from the clip index,
            # which is the only place exact clip boundaries exist.
            announce = detections if detections is not None else clips
            self._fire(index, announce, self._seen_events, window)
            self._download_new(index, camera, clips, window)

        # Status last, and not every poll. It used to run before the detection
        # lookups, so every notification waited on a round trip fetching LED
        # state and storage figures. Poll 0 still fetches it, so nothing is
        # blank on startup.
        if self._polls % self._status_every == 0:
            try:
                self.readings = hub_readings(
                    await self.hass.async_add_executor_job(self.client.hub_status))
            except Exception as err:
                # Status is a bonus; never fail the whole poll over it.
                _LOGGER.debug("Hub status unavailable: %s", err)
        self._polls += 1
        self._primed = True
        # Raise or clear the repair issues. Called every poll rather than only
        # on failure, because an issue that never clears is worse than none.
        try:
            from .repairs import async_check
            async_check(self.hass, self.entry.entry_id, self)
        except Exception as err:  # noqa: BLE001 - never fail a poll over this
            _LOGGER.debug("Could not update repair issues: %s", err)
        return {"clips": clips_by_camera, "hub": self.readings}

    def _fresh(self, index, entries, seen_map, window,
               revisions: bool = False) -> list[dict]:
        """New detections, and detections the hub has since revised.

        The hub revises an entry in place while an event unfolds: someone
        approaches and the entry is motion and a person, then they press the
        doorbell and the same entry -- same start time -- gains the doorbell
        code. Keying only on the start time dropped that second version, so a
        press that followed motion never raised a ring event at all. Polling
        every 2s made this the normal case rather than a rare one; at 20s the
        event was usually over before the first poll saw it.

        So with revisions=True the key is the start time AND what fired. A
        revision is a genuinely new fact and is announced; an unchanged entry
        is still ignored however many times it is polled.

        Downloads pass revisions=False and stay keyed on the start time alone.
        A clip carries whatever detection was attached to it, so keying those
        on the codes too would make a revised detection look like a new clip
        and start the download again.
        """
        seen = seen_map.setdefault(index, set())
        fresh = []
        for entry in entries:
            moment = start_of(entry)
            if moment is None:
                continue
            key = (moment, tuple(detection_types(entry))) if revisions else (moment,)
            if key in seen:
                continue
            seen.add(key)
            fresh.append((moment, entry))
        # Forget anything the poll window can no longer return.
        seen_map[index] = {
            k for k in seen if k[0] >= window - LOOKBACK_SECONDS}
        return [entry for _, entry in sorted(fresh, key=lambda pair: pair[0])]

    def _fire(self, index, entries, seen_map, window) -> None:
        for entry in self._fresh(index, entries, seen_map, window,
                                 revisions=True):
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
        # Doorbell presses survive the cull when a separate figure is set for
        # them: a busy afternoon of motion should not evict the press that was
        # the reason for keeping anything.
        keep_rings = self.entry.options.get(CONF_KEEP_RINGS, DEFAULT_KEEP_RINGS)
        protected: set[int] = set()
        if keep_rings:
            rings = [clip for clip in self.clips_for(index)
                     if event_type(clip) == EVENT_RING]
            protected = {start_of(clip) for clip in rings[:keep_rings]
                         if start_of(clip) is not None}
        for removed in await async_prune(self.hass, camera, keep, protected):
            _LOGGER.debug("Pruned %s to keep the newest %s", removed, keep)
        async_dispatcher_send(self.hass, self.signal("image", index))
