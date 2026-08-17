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

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DATA_HUBS, DOMAIN
from .entity import H500Entity
from .contact_sheet import async_contact_sheet


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    async_add_entities(
        [H500EventImage(hass, coordinator, index, camera)
         for index, camera in enumerate(coordinator.cameras)]
        + [H500ContactSheet(hass, coordinator, index, camera)
           for index, camera in enumerate(coordinator.cameras)]
    )


class H500EventImage(H500Entity, ImageEntity):
    _attr_translation_key = "latest_event"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, coordinator, index: int,
                 camera: dict) -> None:
        H500Entity.__init__(self, coordinator, index, camera)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{camera['device_id']}_latest_event"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.coordinator.signal("event", self.index),
            self._handle))

    @callback
    def _handle(self, kind: str, entry: dict) -> None:
        """Stamp the picture as changed when an event lands.

        Not when the file appears -- there is no watcher on the media
        directory -- but when the hub reports the event that will produce it.
        The download follows within a few seconds, and a stamp that is a
        little early makes the frontend re-fetch, which is the desired effect.
        """
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        # Through the coordinator, so a clip that will never be downloaded --
        # rings-only mode, a download-type filter -- still gets its frame
        # fetched rather than this picture freezing on the last downloaded one.
        return await self.coordinator.async_latest_frame(self.index, self.camera)

    @property
    def image_last_updated(self) -> datetime | None:
        return self._attr_image_last_updated


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

    def __init__(self, hass: HomeAssistant, coordinator, index: int,
                 camera: dict) -> None:
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
