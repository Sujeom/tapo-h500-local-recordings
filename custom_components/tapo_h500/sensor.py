"""Hub and per-camera sensors, built only from responses seen on real hardware."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .clips import (
    ACTIVITY_LEVELS, activity_level, busiest_hour, events_since, hourly_baseline,
    hourly_counts, longest_visit, sessions, unique_faces, unknown_face_count,
    unusual_threshold,
)
from .const import (
    DATA_HUBS, DOMAIN, FACE_PRESENCE_WINDOW, LOITER_GAP, LOOKBACK_SECONDS,
    SIGNAL_FACES_CHANGED,
    PICTURE_RESIGN_SECONDS,
)
from .coordinator import H500Coordinator
from .entity import H500Entity
from .preview import preview_url


@dataclass(frozen=True, kw_only=True)
class HubSensor(SensorEntityDescription):
    value: Callable[[dict], object]
    # What the number leaves out. Only one reading needs it -- a count of
    # custom sound slots says nothing about which sounds -- and the
    # alternative was a whole class for one lambda.
    attributes: Callable[[dict], dict] | None = None


@dataclass(frozen=True, kw_only=True)
class CameraSensor(SensorEntityDescription):
    value: Callable[[H500Coordinator, int, dict], object]


HUB_SENSORS: tuple[HubSensor, ...] = (
    HubSensor(
        key="storage_free", translation_key="storage_free",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda r: r.get("storage_free_gb"),
    ),
    HubSensor(
        key="storage_total", translation_key="storage_total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value=lambda r: r.get("storage_total_gb"),
    ),
    HubSensor(
        key="storage_used", translation_key="storage_used",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda r: r.get("storage_used_percent"),
    ),
    HubSensor(
        key="storage_status", translation_key="storage_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("storage_status"),
    ),
    HubSensor(
        key="siren_time_left", translation_key="siren_time_left",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="s",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("siren_time_left"),
    ),
    HubSensor(
        key="firmware_state", translation_key="firmware_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("firmware_state"),
    ),
    # When the hub installs firmware, which is the half of that setting that
    # actually decides anything. The switch says whether it happens; this says
    # at what hour, and a hub that reboots itself to update at three in the
    # afternoon is worth knowing about before it does.
    #
    # Read from the same block the switch has to send back whole on every
    # toggle, so the two cannot disagree about the schedule.
    HubSensor(
        key="auto_upgrade_time", translation_key="auto_upgrade_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("auto_upgrade_time"),
        attributes=lambda r: {
            # The time is stored whether or not updates are on, so on its own
            # it reads as a schedule that is running when it may not be.
            "enabled": r.get("auto_upgrade"),
            # The hub spreads updates over a window after that time.
            "random_range": (r.get("auto_upgrade_config") or {})
            .get("random_range"),
        },
    ),
    # Clip filenames and the media browser's date folders come from hub
    # timestamps, so drift here files recordings under the wrong day. Signed:
    # ahead and behind are different faults.
    HubSensor(
        key="clock_offset", translation_key="clock_offset",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("clock_offset"),
    ),
    HubSensor(
        key="timezone", translation_key="timezone",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("timezone"),
    ),
    # "off", or the hour the hub reboots itself. A hub on a reboot schedule
    # has a gap in its recordings at that hour, and a gap in recordings is
    # indistinguishable from a camera that has stopped working -- which is
    # precisely what the silent-camera watchdog would report it as.
    #
    # Read only. setReboot stays uncalled: its params are ambiguous between
    # scheduling a reboot and performing one.
    HubSensor(
        key="scheduled_reboot", translation_key="scheduled_reboot",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("scheduled_reboot"),
        attributes=lambda r: {
            "enabled": r.get("scheduled_reboot_enabled"),
            # The hub's own numbering, passed through rather than translated
            # into a weekday: the only value seen was 0, on a schedule that
            # was switched off, which says nothing about what 0 means.
            "day": r.get("scheduled_reboot_day"),
        },
    ),
    # The hub holds five custom sound slots. Empty ones come back as empty
    # strings rather than being absent, so this counts named slots.
    #
    # The names were parsed from the start, documented as an attribute, and
    # never actually attached to anything -- the reference described a field
    # that did not exist. "3" is a poor answer to "which sounds does the hub
    # hold"; the names are the whole content of the reading.
    HubSensor(
        key="custom_sounds", translation_key="custom_sounds",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda r: r.get("custom_sounds"),
        attributes=lambda r: {"names": r.get("custom_sound_names") or []},
    ),
    # Health, so a hub that is quietly struggling is visible before it stops.
    # Assembled from readings already collected rather than new calls.
    HubSensor(
        key="hub_health", translation_key="hub_health",
        value=lambda r: (
            "unreachable" if not r else
            "storage full" if (r.get("storage_used_percent") or 0) >= 99 else
            "storage failing" if r.get("storage_healthy") is False else
            "clock drifted" if abs(r.get("clock_offset") or 0) > 60 else
            "ok"),
    ),
    HubSensor(
        key="ip_address", translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("ip_address"),
    ),
)

CAMERA_SENSORS: tuple[CameraSensor, ...] = (
    CameraSensor(
        key="last_activity", translation_key="last_activity",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda c, i, cam: (
            dt_util.utc_from_timestamp(c.last_activity(i))
            if c.last_activity(i) is not None else None),
    ),
    CameraSensor(
        key="recordings_1h", translation_key="recordings_1h",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="recordings",
        value=lambda c, i, cam: events_since(
            c.clips_for(i), int(dt_util.utcnow().timestamp()) - 3600),
    ),
    CameraSensor(
        key="recordings_24h", translation_key="recordings_24h",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="recordings",
        value=lambda c, i, cam: len(c.clips_for(i)),
    ),
    # Statistics, all with a state class so the recorder keeps them for the
    # long-term graphs. They are computed from the polled window rather than
    # stored: the window is a day, and anything longer is the recorder's job.
    CameraSensor(
        key="busiest_hour", translation_key="busiest_hour",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c, i, cam: busiest_hour(c.clips_for(i)),
    ),
    CameraSensor(
        key="people_seen", translation_key="people_seen",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="people",
        value=lambda c, i, cam: unique_faces(c.clips_for(i)),
    ),
    CameraSensor(
        key="unknown_faces", translation_key="unknown_faces",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="recordings",
        value=lambda c, i, cam: unknown_face_count(c.clips_for(i)),
    ),
    CameraSensor(
        key="ai_enhance", translation_key="ai_enhance",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="%",
        value=lambda c, i, cam: cam.get("ai_enhance"),
    ),
    CameraSensor(
        key="network_mode", translation_key="network_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c, i, cam: cam.get("network_mode"),
    ),
    CameraSensor(
        key="model", translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c, i, cam: cam.get("device_model"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][DATA_HUBS][entry.entry_id]
    entities: list[SensorEntity] = [
        H500HubSensor(coordinator, entry, description)
        for description in HUB_SENSORS
    ]
    entities += [
        H500CameraSensor(coordinator, index, camera, description)
        for index, camera in enumerate(coordinator.cameras)
        for description in CAMERA_SENSORS
    ]
    entities += [
        H500Visits(coordinator, index, camera)
        for index, camera in enumerate(coordinator.cameras)
    ]
    entities += [
        H500ActivityLevel(coordinator, index, camera)
        for index, camera in enumerate(coordinator.cameras)
    ]
    entities.append(H500StorageForecast(coordinator, entry))
    entities.append(H500WedgeClock(coordinator, entry))
    entities.append(H500Household(coordinator, entry))
    async_add_entities(entities)

    # One per named PERSON, added as names appear rather than on a reload.
    # Naming used to reload the whole entry, which cost a hub login and broke
    # whatever was mid-request; now the entry is left alone and this listens
    # for the change instead.
    #
    # Per person rather than per face id: the hub clusters the same person more
    # than once, so naming both clusters "Alice" used to produce two sensors
    # called Alice. The entity is keyed on the lowest id in the group, which
    # for anyone the hub only clustered once is the id it has always been --
    # so nothing already in the registry is orphaned.
    added: set[str] = set()

    @callback
    def _sync_faces() -> None:
        new_ids = []
        for ids in coordinator.named_people.values():
            # Any id of this group already having an entity means the person
            # does. A second cluster gaining the same name joins them rather
            # than adding a duplicate.
            if added.intersection(ids):
                continue
            added.update(ids)
            new_ids.append(ids[0])
        if not new_ids:
            return
        # Two per person: when they were last seen, and where. The pair is
        # what makes following someone between cameras readable at a glance --
        # the hub gives one id per person across the whole house, so "where"
        # is a real answer rather than a guess.
        async_add_entities(
            [H500FaceSensor(coordinator, entry, face_id) for face_id in new_ids]
            + [H500FaceLocationSensor(coordinator, entry, face_id)
               for face_id in new_ids])

    _sync_faces()
    entry.async_on_unload(async_dispatcher_connect(
        hass, f"{SIGNAL_FACES_CHANGED}_{entry.entry_id}", _sync_faces))


class H500HubSensor(CoordinatorEntity[H500Coordinator], SensorEntity):
    """A reading about the hub itself rather than any one camera."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description: HubSensor) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def native_value(self):
        return self.entity_description.value(self.coordinator.readings)

    @property
    def extra_state_attributes(self) -> dict | None:
        """None rather than {} where a reading has nothing to add.

        An empty dictionary is a set of attributes, and Home Assistant records
        it as one on every state change of every hub sensor.
        """
        if self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(self.coordinator.readings)


class H500CameraSensor(H500Entity, SensorEntity):
    def __init__(self, coordinator, index, camera, description: CameraSensor) -> None:
        super().__init__(coordinator, index, camera)
        self.entity_description = description
        self._attr_unique_id = f"{camera['device_id']}_{description.key}"

    @property
    def native_value(self):
        return self.entity_description.value(
            self.coordinator, self.index, self.camera)


class H500Visits(H500Entity, SensorEntity):
    """How many separate visitors this camera saw, rather than how many clips.

    `recordings_24h` counts what the hub filed, which is a different question
    and a misleading one: the hub reports moments, not presence, so a single
    person waiting four minutes at the door produces sixteen recordings. A day
    reading "48 recordings" and a day reading "3 visits" can be the same day.

    Its own class rather than another entry in CAMERA_SENSORS because those
    carry one value each and the useful part here is the attributes -- the
    shape of the day and the longest anybody stayed.
    """

    _attr_translation_key = "visits_24h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "visits"
    _attr_icon = "mdi:account-clock"

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_visits_24h"

    @property
    def _clips(self) -> list[dict]:
        return self.coordinator.clips_for(self.index)

    @property
    def native_value(self) -> int:
        return len(sessions(self._clips, LOITER_GAP))

    @property
    def extra_state_attributes(self) -> dict:
        return {
            # 24 numbers from local midnight. A card can draw this straight;
            # `busiest_hour` is the same data reduced to its peak, and loses
            # the difference between a steady afternoon and one loud minute.
            "hourly": hourly_counts(self._clips),
            # First sighting to last, so a lone fifteen-second clip counts as
            # fifteen seconds rather than as however long ago it was.
            "longest_seconds": longest_visit(self._clips, LOITER_GAP),
            # What the grouping was, so a surprising count is explainable
            # without reading the source.
            "gap_seconds": LOITER_GAP,
        }


class H500ActivityLevel(H500Entity, SensorEntity):
    """The last hour at this camera in one word.

    Answering "is anything going on at the side gate" currently means reading
    a recordings count, an unusual-activity flag and a last-activity timestamp
    and joining them up by eye -- and each of those is a different question.
    This is the join, made once, so a dashboard and an automation cannot reach
    different conclusions from the same three numbers.

    Judged against the camera's own recent rate, like the unusual flag, and
    with the same per-camera sensitivity: a doorbell facing a pavement and a
    back gate do not agree on what busy means. `busy` is exactly halfway to
    `unusual` rather than a second pair of numbers, so the scale cannot go
    backwards.
    """

    _attr_translation_key = "activity_level"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ACTIVITY_LEVELS)
    _attr_icon = "mdi:motion-sensor"

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_activity_level"

    @property
    def native_value(self) -> str:
        multiplier, floor = self.coordinator.sensitivity(self.index)
        return activity_level(
            self.coordinator.clips_for(self.index),
            int(dt_util.utcnow().timestamp()),
            LOOKBACK_SECONDS, multiplier, floor)

    @property
    def extra_state_attributes(self) -> dict:
        clips = self.coordinator.clips_for(self.index)
        now = int(dt_util.utcnow().timestamp())
        multiplier, floor = self.coordinator.sensitivity(self.index)
        threshold = unusual_threshold(
            clips, now, LOOKBACK_SECONDS, multiplier, floor)
        return {
            "events_last_hour": events_since(clips, now - 3600),
            "typical_per_hour": round(
                hourly_baseline(clips, now, LOOKBACK_SECONDS), 2),
            # The two numbers the word was decided against, so "why does this
            # say active" is answerable from the entity rather than the source.
            "busy_at": round(threshold / 2, 2),
            "unusual_at": round(threshold, 2),
        }


def hub_device(coordinator: H500Coordinator, entry: ConfigEntry):
    from homeassistant.helpers.device_registry import DeviceInfo

    # Fetched once at connect rather than polled: pytapo asks getDeviceInfo to
    # work out what it is talking to, and the answer was being thrown away
    # after one model check. It costs no round trip to keep it.
    info = coordinator.client.info
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="TP-Link",
        # The hub's own word for itself, falling back to what this integration
        # only supports anyway.
        model=info.get("device_model") or "H500",
        # None where the hub did not say, which is what every other reading
        # here does. Home Assistant simply leaves the field off the device
        # page rather than showing "unknown".
        sw_version=info.get("sw_version"),
        hw_version=info.get("hw_version"),
        configuration_url=f"https://{entry.data.get('host')}",
    )


class H500StorageForecast(CoordinatorEntity[H500Coordinator], SensorEntity):
    """Days until the hub starts overwriting its oldest recordings.

    Not a failure when it arrives -- loop recording does not stop at full, it
    silently discards the oldest footage -- which is exactly why the warning
    has to come early enough to download anything worth keeping.

    Its own class rather than another entry in HUB_SENSORS because those are
    computed from one status response and this needs the run of them the
    coordinator has been collecting.

    Unavailable, rather than a large number, whenever the answer is not known:
    for the first hour after a restart there is not enough history, and a hub
    already overwriting sits at a steady figure forever, where a line fitted
    to the rounding noise would say something like "full in 4000 days".
    """

    _attr_has_entity_name = True
    _attr_translation_key = "storage_full_in"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:harddisk"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_storage_full_in"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def native_value(self):
        return self.coordinator.days_until_full()

    @property
    def extra_state_attributes(self) -> dict:
        from .status import fill_rate
        rate = fill_rate(self.coordinator.storage_trend)
        return {
            # Why the forecast says what it says, and why it sometimes says
            # nothing: a reading of "measuring" here is the difference between
            # "not filling" and "not enough history yet".
            "percent_per_hour": None if rate is None else round(rate, 4),
            "samples": len(self.coordinator.storage_trend),
        }


class H500WedgeClock(CoordinatorEntity[H500Coordinator], SensorEntity):
    """How long the hub has been serving recordings, this run.

    The hub stops serving video every so often and keeps no record of having
    done it. Neither does Home Assistant in any form that lasts: the wedge
    binary sensor beside this one says whether it is happening now, and binary
    sensors get no long-term statistics, so its history ends at the recorder's
    purge -- ten days, where the question worth asking is whether this hub is
    getting worse over months.

    A number does get kept forever, so this is the same fact as a number.
    Climbing while the hub serves, zero while it does not: the long-term graph
    is a sawtooth whose peaks are the times to wedge and whose resets are the
    wedges. How often, how long between, and what the best run was, all read
    off one line -- which is what a support case needs and what nobody could
    produce from memory.

    Hours rather than seconds because the observed gap is about twelve of
    them, and a seconds axis would be unreadable at the span that matters.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "media_healthy_for"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-alert-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_media_healthy_for"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def native_value(self) -> float:
        return self.coordinator.healthy_seconds / 3600

    @property
    def extra_state_attributes(self) -> dict:
        """The counts the graph makes you squint at.

        Since this Home Assistant started, not since the hub was made: none of
        it is written to disk, and a number that pretended to span restarts
        would be the more misleading of the two.
        """
        coordinator = self.coordinator
        return {
            "wedges_7d": coordinator.wedges_since(7 * 86400),
            "wedges_24h": coordinator.wedges_since(86400),
            "longest_healthy_hours":
                round(coordinator.longest_healthy_seconds / 3600, 1),
            "last_wedge": (
                dt_util.utc_from_timestamp(coordinator.wedges[-1]).isoformat()
                if coordinator.wedges else None),
        }


class H500Household(CoordinatorEntity[H500Coordinator], SensorEntity):
    """How many of the people you have named were seen in the last few minutes.

    One entity per person is the right shape for automating and the wrong one
    for looking at: with five people named, "is anybody about" means reading
    five sensors and comparing five timestamps by eye.

    Named for what it knows, like the per-person flag it sums up. A camera
    watches a doorstep, not a house -- somebody indoors is invisible to it, and
    so is somebody who left through a door with no camera on it. `not_seen` is
    a list of people who have not been seen, which is not a list of people who
    are out, and building an occupancy automation on it would be building on a
    guess.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "people_seen_recently"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "people"
    _attr_icon = "mdi:account-group"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_people_seen_recently"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def native_value(self) -> int:
        return len(self.coordinator.household(
            FACE_PRESENCE_WINDOW)["seen_recently"])

    @property
    def extra_state_attributes(self) -> dict:
        return {
            **self.coordinator.household(FACE_PRESENCE_WINDOW),
            # Everyone who could appear in those lists, so an empty house and
            # an installation where nobody has been named yet are different
            # readings rather than both being zero.
            "named": sorted(self.coordinator.named_people),
            "window_minutes": FACE_PRESENCE_WINDOW // 60,
        }


class H500FaceSensor(CoordinatorEntity[H500Coordinator], SensorEntity):
    """When a named person was last seen, across every camera.

    One per name in the shared map rather than one per id the hub has ever
    emitted: the hub invents an id for every face it clusters, including
    passers-by, and an entity per stranger would fill the registry with
    numbered ghosts that never return. Naming someone is the signal that they
    are worth tracking.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry, face_id: str) -> None:
        super().__init__(coordinator)
        self.face_id = str(face_id)
        self._attr_unique_id = f"{entry.entry_id}_face_{self.face_id}"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def name(self) -> str:
        """Read live, so renaming someone takes effect without a reload.

        A name captured at construction would leave the entity showing the old
        one until the integration restarted -- and avoiding that restart is the
        point of this whole path.
        """
        return self.coordinator.face_names.get(self.face_id) or f"Face {self.face_id}"

    @property
    def _face(self) -> dict:
        # The whole person, merged across every cluster the hub gave them.
        return self.coordinator.person_for(self.face_id)

    @property
    def native_value(self):
        seen = self._face.get("last_seen")
        return dt_util.utc_from_timestamp(seen) if seen else None

    @property
    def entity_picture(self) -> str | None:
        """Their photograph: the frame of their newest sighting.

        Served through the preview endpoint, which generates the frame from
        the hub if no download ever wrote it and caches it on disk after
        the first look. The signed URL is cached per sighting and re-signed
        only as its signature nears expiry -- a fresh signature per poll
        would make the frontend refetch the same photograph every two
        seconds.
        """
        face = self._face
        seen, index = face.get("last_seen"), face.get("camera_index")
        if seen is None or index is None or self.hass is None:
            return None
        now = dt_util.utcnow().timestamp()
        if (getattr(self, "_picture_for", None) != seen
                or now - getattr(self, "_picture_signed", 0)
                > PICTURE_RESIGN_SECONDS):
            self._picture = preview_url(
                self.hass, self.coordinator.entry.entry_id, index, seen)
            self._picture_for, self._picture_signed = seen, now
        return self._picture

    @property
    def extra_state_attributes(self) -> dict:
        face = self._face
        return {
            "face_id": self.face_id,
            # Every cluster that is this person. Matching on one id alone
            # misses half their sightings, which is exactly the bug this
            # merging exists to fix.
            "face_ids": face.get("ids") or [self.face_id],
            # Within the poll window only, which is what every other count in
            # this integration means; a lifetime total would need a database.
            "sightings": face.get("sightings", 0),
            "cameras": face.get("cameras", []),
            # The oldest sighting still in the window, which on an ordinary
            # day is when they first appeared. It is what separates "they got
            # home" from the eleventh time they crossed the front camera.
            "first_seen": (
                dt_util.utc_from_timestamp(face["first_seen"]).isoformat()
                if face.get("first_seen") else None),
        }


class H500FaceLocationSensor(CoordinatorEntity[H500Coordinator], SensorEntity):
    """Which camera last saw this person, and the trail of ones before it.

    Face ids are hub-wide rather than per-camera -- measured on this hardware,
    two of six ids appeared on both doorbells -- so the same number really does
    follow one person from door to door. This is that, surfaced.

    It reports where the hub last SAW someone, which is not where they are.
    Nobody is tracked between sightings and a quiet camera means nothing was
    detected, not that the person left; the state simply stops changing.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:map-marker-account"

    def __init__(self, coordinator, entry, face_id: str) -> None:
        super().__init__(coordinator)
        self.face_id = str(face_id)
        self._attr_unique_id = f"{entry.entry_id}_face_{self.face_id}_location"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def name(self) -> str:
        who = self.coordinator.face_names.get(self.face_id) \
            or f"Face {self.face_id}"
        return f"{who} last seen at"

    @property
    def _face(self) -> dict:
        # The whole person, merged across every cluster the hub gave them.
        return self.coordinator.person_for(self.face_id)

    @property
    def native_value(self):
        # None rather than "unknown" or a stale camera: outside the polled
        # window there is genuinely no answer, and inventing one would read as
        # "they are at the front door" long after they left.
        return self._face.get("last_camera")

    @property
    def extra_state_attributes(self) -> dict:
        face = self._face
        trail = face.get("trail") or []
        return {
            "face_id": self.face_id,
            "face_ids": face.get("ids") or [self.face_id],
            "cameras": face.get("cameras", []),
            "sightings": face.get("sightings", 0),
            # "approaching", "leaving", or absent when it is not known --
            # which is the usual case until cameras are given an order.
            "direction": face.get("direction"),
            # Newest first: camera and when, so a history of one person moving
            # between doors is readable without joining anything up by hand.
            "trail": [{"camera": hop["camera"],
                       "at": dt_util.utc_from_timestamp(hop["at"]).isoformat()}
                      for hop in trail],
        }
