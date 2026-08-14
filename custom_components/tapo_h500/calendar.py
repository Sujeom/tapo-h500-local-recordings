"""Every detection as a calendar entry, so a day can be read at a glance.

The cards show recent activity and the media browser shows what has been
downloaded. Neither answers "what happened last Tuesday" without knowing to
look at last Tuesday first, and Home Assistant already has a panel built for
exactly that question.

Deliberately reads from the hub rather than from the polled window. The
coordinator holds a day of recordings, so a calendar built on it would show a
day and then nothing -- which is worse than no calendar, because scrolling
back would suggest a quiet fortnight rather than an absent one. The hub keeps
weeks, and one lookup per view is cheap: measured on firmware 1.3.20, a
detection search is about 17ms.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .clips import describe_detection, end_of, face_ids, start_of
from .const import DATA_HUBS, DOMAIN
from .entity import H500Entity

_LOGGER = logging.getLogger(__name__)

# The longest span to ask the hub about in one go.
#
# The calendar panel decides its own range, and a year view would ask for a
# year. searchDetectionList caps at 1000 records anyway, so a huge window
# silently returns a truncated answer -- better to bound it here and say so in
# the log than to show a month of a year and call it the year.
MAX_SPAN = timedelta(days=31)

# A detection with no end time still needs a length, or the calendar draws a
# zero-width entry that cannot be clicked. Clips on this hardware run about
# fifteen seconds.
ASSUMED_SECONDS = 15


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    async_add_entities(
        H500Calendar(coordinator, index, camera)
        for index, camera in enumerate(coordinator.cameras)
    )


class H500Calendar(H500Entity, CalendarEntity):
    _attr_translation_key = "recordings"

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_calendar"

    @property
    def event(self) -> CalendarEvent | None:
        """The most recent detection, from the window already polled.

        Home Assistant asks for the current or next entry. A doorbell has no
        future -- nothing here can say what will happen -- and a recording is
        indexed only once it has finished, so "current" is almost never true
        either. The most recent one is the only useful answer to the question,
        and it leaves the entity's own state off, which is correct: nothing is
        happening right now as far as anything can tell.
        """
        clips = self.coordinator.clips_for(self.index)
        newest, latest = None, None
        for clip in clips:
            moment = start_of(clip)
            if moment is not None and (latest is None or moment > latest):
                newest, latest = clip, moment
        return None if newest is None else self._entry(newest)

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime,
                               end_date: datetime) -> list[CalendarEvent]:
        if end_date - start_date > MAX_SPAN:
            _LOGGER.debug(
                "Calendar asked for %s of %s; showing the most recent %s",
                end_date - start_date, self.camera.get("alias"), MAX_SPAN)
            start_date = end_date - MAX_SPAN
        begin, until = int(start_date.timestamp()), int(end_date.timestamp())
        try:
            entries = await hass.async_add_executor_job(
                self.coordinator.client.detections, self.camera, begin, until)
        except Exception as err:  # noqa: BLE001 - an empty view beats an error
            _LOGGER.debug("Could not read the detection log: %s", err)
            return []
        # None means the hub has no detection log at all. The clip index still
        # says when something was recorded, which is most of the answer.
        if entries is None:
            try:
                entries = await hass.async_add_executor_job(
                    self.coordinator.client.recent, self.camera, begin, until)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Could not list recordings: %s", err)
                return []
        found = [self._entry(entry) for entry in entries or []
                 if start_of(entry) is not None]
        found.sort(key=lambda item: item.start)
        return found

    def _entry(self, entry: dict) -> CalendarEvent:
        began = start_of(entry)
        finished = end_of(entry)
        # An entry with no length, or a negative one from a hub whose clock
        # moved, would draw as a zero-width block nothing can be clicked on.
        if finished is None or finished <= began:
            finished = began + ASSUMED_SECONDS
        return CalendarEvent(
            start=dt_util.as_local(dt_util.utc_from_timestamp(began)),
            end=dt_util.as_local(dt_util.utc_from_timestamp(finished)),
            summary=self._summary(entry),
            description=self._description(entry),
            location=self.camera.get("alias") or None,
        )

    def _summary(self, entry: dict) -> str:
        """What happened, and who, in as few words as a calendar row allows."""
        what = describe_detection(entry) or "Activity"
        names = self.coordinator.face_names
        who = sorted({names[str(face)] for face in face_ids(entry)
                      if str(face) in names})
        return f"{what.capitalize()} — {', '.join(who)}" if who \
            else what.capitalize()

    def _description(self, entry: dict) -> str | None:
        """The face ids, for anyone chasing somebody the hub has not been
        told the name of. Nothing at all when there were none, rather than an
        empty label."""
        found = face_ids(entry)
        if not found:
            return None
        return "Faces: " + ", ".join(str(face) for face in found)
