"""One still per camera: the frame from its most recent event.

Home Assistant's `image` platform is the right shape for this and `camera` is
not. A camera entity promises a live feed, offers a stream and a snapshot
service, and appears wherever the frontend expects live video -- none of which
this hub can do, because a media session opens, is acknowledged, and the
camera never wakes to send anything.

An image entity promises exactly what there is: a picture, and the time it was
taken. That timestamp is the useful part. `image_last_updated` tells the
frontend the picture changed, so a dashboard refreshes when an event lands
rather than polling, and anyone looking at it can see how old it is instead of
wondering whether they are looking at now.

The camera entity is kept: it works in picture cards and existing dashboards,
and removing it would break setups that use it.
"""
from __future__ import annotations

from .models import Camera

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util


from .coordinator import H500Coordinator
from .entity import add_cameras_as_they_appear, H500Entity
from .contact_sheet import async_contact_sheet

# Unlimited: nothing here polls the hub. Every value comes from the
# coordinator's one poll, so there is nothing to serialise.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    add_cameras_as_they_appear(
        coordinator, entry, async_add_entities,
        lambda index, camera: [
            H500EventImage(hass, coordinator, index, camera),
            H500ContactSheet(hass, coordinator, index, camera)])


class H500EventImage(H500Entity, ImageEntity):
    _attr_translation_key = "latest_event"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, coordinator: H500Coordinator, index: int,
                 camera: Camera) -> None:
        H500Entity.__init__(self, coordinator, index, camera)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{camera['device_id']}_latest_event"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.coordinator.signal("event", self.index),
            self._handle))
        # And again when the download actually writes the file. The event
        # stamp alone made the frontend fetch several seconds before any
        # frame of that event existed -- it got the previous one and was
        # never told to look again.
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.coordinator.signal("image", self.index),
            self._stamp))

    @callback
    def _handle(self, kind: str, entry: dict) -> None:
        """Stamp the picture as changed when an event lands.

        Early on purpose: the coordinator fetches the frame on demand now, so
        a fetch triggered by this stamp usually finds it. The second stamp in
        _stamp covers the fetch that raced the download and lost.
        """
        self._stamp()

    @callback
    def _stamp(self) -> None:
        """Timestamp the picture with when it was TAKEN, not when we looked.

        `utcnow()` here was the whole reason a frame from last night read as
        seconds old: the frontend was being told the picture had just changed
        every time anything asked it to look. That is the exact confusion the
        module docstring says this timestamp exists to prevent, so it now
        reports the newest event's own moment.

        Home Assistant re-fetches when this value changes, and it still does:
        a new event means a newer moment. Two stamps for the SAME event now
        collapse to one value, which is correct -- the second stamp exists for
        the download landing after the fetch, and by then the entity has the
        frame it was waiting for either way. Falls back to now() only when the
        camera has produced nothing at all, where there is no truer answer.
        """
        moment = self.coordinator.last_activity(self.index)
        self._attr_image_last_updated = (
            dt_util.utc_from_timestamp(moment) if moment is not None
            else dt_util.utcnow())
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        # Through the coordinator, so a clip that will never be downloaded --
        # rings-only mode, a download-type filter -- still gets its frame
        # fetched rather than this picture freezing on the last downloaded one.
        return await self.coordinator.async_latest_frame(self.index, self.camera)

    @property
    def image_last_updated(self) -> datetime | None:
        return self._attr_image_last_updated

    @property
    def extra_state_attributes(self) -> dict:
        """How old the picture is, in the words a person would use.

        A camera that has wedged keeps serving its last frame forever, and a
        still picture cannot say so itself. This can: the age is the signal
        that what you are looking at is not what is happening now.
        """
        moment = self.coordinator.last_activity(self.index)
        if moment is None:
            return {"frame_taken": None, "frame_age_seconds": None}
        age = max(0, int(dt_util.utcnow().timestamp()) - moment)
        return {
            "frame_taken": dt_util.utc_from_timestamp(moment).isoformat(),
            "frame_age_seconds": age,
        }


class H500ContactSheet(H500Entity, ImageEntity):
    """Today's recordings, all at once.

    A doorbell produces dozens of near-identical fifteen-second clips a day,
    and looking through them means opening dozens of things. Every frame at
    once, small and in order, is the oldest answer to that and still the best
    one: the recording that matters gets found by looking rather than by
    clicking.

    Unavailable on a quiet day rather than showing a blank sheet, which would
    read as a fault.
    """

    _attr_translation_key = "contact_sheet"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, coordinator: H500Coordinator, index: int,
                 camera: Camera) -> None:
        H500Entity.__init__(self, coordinator, index, camera)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{camera['device_id']}_contact_sheet"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The download signal rather than the event one: a sheet is built from
        # thumbnails, and a thumbnail is written by the download. Stamping on
        # the event would make the frontend re-fetch an unchanged picture
        # several seconds before the new frame exists.
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.coordinator.signal("image", self.index),
            self._handle))

    @callback
    def _handle(self) -> None:
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        # Built on request rather than kept in memory. Home Assistant fetches
        # only when image_last_updated changes, so this runs about as often as
        # a clip arrives -- and holding a few hundred kilobytes per camera for
        # a picture nobody may look at is the wrong trade.
        return await async_contact_sheet(
            self.hass, self.camera,
            dt_util.as_local(dt_util.utcnow()).date().isoformat())

    @property
    def image_last_updated(self) -> datetime | None:
        return self._attr_image_last_updated
