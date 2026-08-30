"""Hub and per-camera on/off state."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .clips import (
    detection_types, expected_since, likely_delivery, loitering,
    unusually_busy,
)
from .const import (
    CONF_NIGHT_END, CONF_NIGHT_START, CONF_SILENT_HOURS, DEFAULT_NIGHT_END, DEFAULT_NIGHT_START, DEFAULT_SILENT_HOURS,
    DELIVERY_HOLD, DELIVERY_SECONDS, DETECTION_HOLD,
    DETECTION_NAMES, FACE_PRESENCE_WINDOW,
    LOITER_GAP, LOITER_SECONDS,
    LOOKBACK_SECONDS, SIGNAL_FACES_CHANGED, SILENT_EXPECTED,
)
from .coordinator import H500Coordinator
from .entity import add_cameras_as_they_appear, H500Entity
from .sensor import hub_device

# Unlimited: nothing here polls the hub. Every value comes from the
# coordinator's one poll, so there is nothing to serialise.
PARALLEL_UPDATES = 0



@dataclass(frozen=True, kw_only=True)
class HubFlag(BinarySensorEntityDescription):
    value: Callable[[dict], bool | None]


@dataclass(frozen=True, kw_only=True)
class CameraFlag(BinarySensorEntityDescription):
    value: Callable[[dict], bool | None]


# Only readings with no control of their own live here. The siren, LED and loop
# recording moved to siren/switch entities, which carry the same state and can
# also change it; keeping a read-only twin of each was two entities per fact.
# Media encryption stays because it is deliberately not writable — changing it
# would break the download path.
HUB_FLAGS: tuple[HubFlag, ...] = (
    HubFlag(
        key="storage_problem", translation_key="storage_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # Inverted deliberately: PROBLEM is on when unhealthy.
        value=lambda r: None if r.get("storage_healthy") is None
        else not r["storage_healthy"],
    ),
    HubFlag(
        key="media_encrypted", translation_key="media_encrypted",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda r: r.get("media_encrypted"),
    ),
)

CAMERA_FLAGS: tuple[CameraFlag, ...] = (
    CameraFlag(
        key="hub_storage", translation_key="hub_storage",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda cam: cam.get("hub_storage_enabled"),
    ),
    CameraFlag(
        key="continuous_recording", translation_key="continuous_recording",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda cam: cam.get("plan_24h_record"),
    ),
    CameraFlag(
        key="ai_enhance_enabled", translation_key="ai_enhance_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda cam: cam.get("AI_enhance_enabled"),
    ),
    CameraFlag(
        key="wifi_backup", translation_key="wifi_backup",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda cam: cam.get("wifi_backup_enabled"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        H500HubFlag(coordinator, entry, description) for description in HUB_FLAGS
    ]
    entities.append(H500Prowling(coordinator, entry))
    entities.append(H500MediaProblem(coordinator, entry))
    entities.append(H500CamerasDark(coordinator, entry))
    async_add_entities(entities)

    def _for_camera(index, camera) -> list[BinarySensorEntity]:
        return (
            [H500CameraFlag(coordinator, index, camera, description)
             for description in CAMERA_FLAGS]
            + [H500UnusualActivity(coordinator, index, camera),
               H500Loitering(coordinator, index, camera),
               H500CameraSilent(coordinator, index, camera),
               H500Delivery(coordinator, index, camera)]
            + [H500DetectionFlag(coordinator, index, camera, code)
               for code in DETECTION_NAMES])

    add_cameras_as_they_appear(
        coordinator, entry, async_add_entities, _for_camera)

    # One per named person, added as names appear rather than on a reload, the
    # same way the face sensors are -- and keyed the same way, on the lowest id
    # of the group, so two clusters of one person are one entity.
    added: set[str] = set()

    @callback
    def _sync_faces() -> None:
        fresh = []
        for ids in coordinator.named_people.values():
            if added.intersection(ids):
                continue
            added.update(ids)
            fresh.append(ids[0])
        if not fresh:
            return
        async_add_entities(
            H500FaceSeenRecently(coordinator, entry, face_id)
            for face_id in fresh)

    _sync_faces()
    entry.async_on_unload(async_dispatcher_connect(
        hass, f"{SIGNAL_FACES_CHANGED}_{entry.entry_id}", _sync_faces))


class H500HubFlag(CoordinatorEntity[H500Coordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description: HubFlag) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.coordinator.readings)


class H500CameraFlag(H500Entity, BinarySensorEntity):
    def __init__(self, coordinator, index, camera, description: CameraFlag) -> None:
        super().__init__(coordinator, index, camera)
        self.entity_description = description
        self._attr_unique_id = f"{camera['device_id']}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        # The paired-device record is refreshed on every poll.
        current = self.coordinator.cameras[self.index] \
            if self.index < len(self.coordinator.cameras) else self.camera
        return self.entity_description.value(current)


# What each detection is, in Home Assistant's own vocabulary, so the frontend
# picks a sensible icon and wording. Most codes have no matching class -- there
# is no "vehicle" or "pet" device class -- and inventing one would be worse
# than leaving it plain.
DETECTION_CLASSES: dict[int, BinarySensorDeviceClass] = {
    2: BinarySensorDeviceClass.MOTION,
    6: BinarySensorDeviceClass.OCCUPANCY,
    19: BinarySensorDeviceClass.TAMPER,
    20: BinarySensorDeviceClass.OCCUPANCY,
    22: BinarySensorDeviceClass.OCCUPANCY,
}


class H500DetectionFlag(H500Entity, BinarySensorEntity):
    """On while the hub has recently reported this detection.

    Driven by the same dispatcher signal as the event entity, so it turns on
    at the same instant a notification fires rather than waiting for the next
    poll. It clears itself after DETECTION_HOLD, because the hub reports that
    something happened and never reports that it stopped.
    """

    def __init__(self, coordinator, index: int, camera: dict, code: int) -> None:
        super().__init__(coordinator, index, camera)
        self._code = code
        slug = DETECTION_NAMES[code].replace(" ", "_")
        self._attr_translation_key = f"detected_{slug}"
        self._attr_unique_id = f"{camera['device_id']}_detected_{slug}"
        self._attr_device_class = DETECTION_CLASSES.get(code)
        self._attr_is_on = False
        self._clear_timer = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self.coordinator.signal("event", self.index), self._handle))
        # A pending timer firing against a removed entity would raise.
        self.async_on_remove(self._cancel_timer)

    @callback
    def _cancel_timer(self) -> None:
        if self._clear_timer is not None:
            self._clear_timer()
            self._clear_timer = None

    @callback
    def _handle(self, kind: str, entry: dict) -> None:
        if self._code not in detection_types(entry):
            return
        # Restart the hold rather than letting the first detection's timer end
        # it: a visitor who keeps triggering should read as one presence.
        self._cancel_timer()
        self._attr_is_on = True
        self.async_write_ha_state()
        self._clear_timer = async_call_later(
            self.hass, DETECTION_HOLD, self._clear)

    @callback
    def _clear(self, _now) -> None:
        self._clear_timer = None
        self._attr_is_on = False
        self.async_write_ha_state()


class H500UnusualActivity(H500Entity, BinarySensorEntity):
    """On when the last hour stands out against this camera's own recent rate.

    Compared against the camera itself rather than a fixed number, because a
    doorbell on a main road and a back gate disagree about what busy means.
    The baseline can only come from the polled window -- this integration holds
    a day of recordings, not a database -- so it is a same-day comparison, and
    the entity says so rather than implying weeks of history.
    """

    _attr_translation_key = "unusual_activity"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_unusual_activity"

    @property
    def is_on(self) -> bool:
        from homeassistant.util import dt as dt_util
        multiplier, floor = self.coordinator.sensitivity(self.index)
        return unusually_busy(
            self.coordinator.clips_for(self.index),
            int(dt_util.utcnow().timestamp()),
            LOOKBACK_SECONDS, multiplier, floor)

    @property
    def extra_state_attributes(self) -> dict:
        from homeassistant.util import dt as dt_util
        from .clips import events_since, hourly_baseline
        clips = self.coordinator.clips_for(self.index)
        now = int(dt_util.utcnow().timestamp())
        multiplier, floor = self.coordinator.sensitivity(self.index)
        return {
            "events_last_hour": events_since(clips, now - 3600),
            "typical_per_hour": round(
                hourly_baseline(clips, now, LOOKBACK_SECONDS), 2),
            # What it is being measured against, so "why has this not fired"
            # is answerable from the entity rather than from the source.
            "multiplier": multiplier,
            "minimum_per_hour": floor,
        }


class H500Loitering(H500Entity, BinarySensorEntity):
    """On while an unrecognised face has been at this camera for a while.

    The one signal here about how long somebody stayed. Everything else counts
    events: the busy-camera flag is a rate over an hour, the night flag is
    about the clock, and both would read a four-minute wait at the door exactly
    the same as somebody walking past. A visit is what tells them apart, and
    the hub does not report visits -- it reports moments, so they have to be
    grouped back together.
    """

    _attr_translation_key = "loitering"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_loitering"

    @property
    def _seconds(self) -> int:
        from homeassistant.util import dt as dt_util
        return loitering(
            self.coordinator.clips_for(self.index),
            int(dt_util.utcnow().timestamp()), LOITER_GAP, LOITER_SECONDS)

    @property
    def is_on(self) -> bool:
        return self._seconds > 0

    @property
    def extra_state_attributes(self) -> dict:
        # Zero rather than absent when nobody is there: an automation reading
        # this in a template gets a number either way.
        return {"seconds": self._seconds}


def silent_threshold(coordinator) -> int:
    """The configured silence threshold in seconds, capped at the window.

    Capped rather than validated away: the option can only be set within
    range, but an entry saved before the range existed could hold anything,
    and a threshold beyond the window would make the sensor permanently off
    for a reason nobody could see.
    """
    hours = coordinator.entry.options.get(
        CONF_SILENT_HOURS, DEFAULT_SILENT_HOURS)
    try:
        seconds = int(hours) * 3600
    except (TypeError, ValueError):
        seconds = DEFAULT_SILENT_HOURS * 3600
    return min(max(3600, seconds), LOOKBACK_SECONDS)


def expected_events(coordinator, index: int) -> float:
    """Events this camera's own history predicted during the silence."""
    last = coordinator.last_activity(index)
    if last is None:
        return 0.0
    from homeassistant.util import dt as dt_util
    return expected_since(
        coordinator.clips_for(index), last,
        int(dt_util.utcnow().timestamp()), LOOKBACK_SECONDS)


def camera_is_silent(coordinator, index: int) -> bool | None:
    """Whether this camera has stopped producing. None before the first poll.

    A function rather than a method because two sensors ask it -- this
    camera's own, and the hub-wide one that only fires when every camera says
    yes at once. Two copies of the rule would let the dashboard show every
    camera silent while the hub sensor stayed off.
    """
    seconds = coordinator.silent_seconds(index)
    # None reads as "unknown" in the frontend, which is the truth, where
    # False would say "fine".
    if seconds is None:
        return None
    # Two grounds: the configured ceiling, and the camera's own expectation --
    # a busy doorbell that should have produced three events by now is flagged
    # in hours, not in a day. The adaptive half can only ever flag EARLIER.
    tripped = (seconds >= silent_threshold(coordinator)
               or expected_events(coordinator, index) >= SILENT_EXPECTED)
    # Latched, because the expectation decays: its baseline is the clips still
    # inside the poll window, and they age out while the camera stays dark.
    # Without this the sensor trips in the evening and reads healthy again by
    # midnight, with the camera still dead.
    return coordinator.latch_silent(index, tripped)


class H500CameraSilent(H500Entity, BinarySensorEntity):
    """On when this camera has produced nothing for longer than expected.

    A camera that has fallen off the Wi-Fi, run flat or been unplugged is
    invisible here: the hub's paired-device record has 16 fields and not one
    of them is an online flag, a signal strength or a battery -- measured, and
    written up in the protocol notes beside the eleven battery methods that
    all answer -40106. Every entity simply keeps showing its last value, which
    looks exactly like a quiet week.

    So this watches for silence, which is the only evidence there is, and is
    named for what it actually knows. A back gate that genuinely sees nobody
    for a day will trip it, which is why the threshold is adjustable.
    """

    _attr_translation_key = "camera_silent"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_silent"

    def _expected(self) -> float:
        return expected_events(self.coordinator, self.index)

    @property
    def is_on(self) -> bool | None:
        return camera_is_silent(self.coordinator, self.index)

    @property
    def extra_state_attributes(self) -> dict:
        seconds = self.coordinator.silent_seconds(self.index)
        return {
            "silent_seconds": seconds,
            "threshold_hours": silent_threshold(self.coordinator) // 3600,
            # Why it is on, when it is on early -- and how close it is when
            # it is not.
            "expected_events": round(self._expected(), 1),
            # ...and why it is still on once that number has decayed away,
            # which otherwise reads as a sensor stuck for no reason.
            "held_since_last_recording": self.coordinator.silent_latched(
                self.index),
        }


class H500Delivery(H500Entity, BinarySensorEntity):
    """On for a few minutes after a visit that looked like a delivery.

    Somebody was there, the hub did not recognise them, and they did not stay
    -- in daylight. That is a courier far more often than anything else.

    Retrospective, which is the part worth understanding: at the moment the
    hub reports a detection the person has been there for one clip, and so has
    everybody about to stay for ten minutes. The length of a visit is only
    known once it has ended, so this cannot answer "is that a delivery at my
    door right now". It answers "was that a delivery", and holds the answer
    long enough for an automation to see it.
    """

    _attr_translation_key = "possible_delivery"
    _attr_icon = "mdi:package-variant-closed"

    def __init__(self, coordinator, index: int, camera: dict) -> None:
        super().__init__(coordinator, index, camera)
        self._attr_unique_id = f"{camera['device_id']}_possible_delivery"

    @property
    def is_on(self) -> bool:
        from homeassistant.util import dt as dt_util
        now = dt_util.utcnow()
        options = self.coordinator.entry.options
        return likely_delivery(
            self.coordinator.clips_for(self.index),
            int(now.timestamp()), LOITER_GAP, DELIVERY_SECONDS, DELIVERY_HOLD,
            dt_util.as_local(now).hour,
            options.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
            options.get(CONF_NIGHT_END, DEFAULT_NIGHT_END))


class H500Prowling(CoordinatorEntity[H500Coordinator], BinarySensorEntity):
    """On when somebody has been round the house rather than up to the door.

    A visitor passes each camera once. Somebody circling comes back to one
    they have already been past, and that return is the whole signal -- it
    needs no camera layout, so it works before anyone has filled one in.

    On the hub rather than a camera, because a circuit is by definition not
    about one camera. The `faces` attribute says who, using their name where
    they have one.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "prowling"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_prowling"
        self._attr_device_info = hub_device(coordinator, entry)

    def _circling(self) -> list[dict]:
        """Everyone in the window whose trail comes back on itself.

        Merging matters most here. Somebody the hub clustered twice walks
        front, side, front and each cluster holds half the trail, so neither
        half contains the return that is the entire signal -- a circuit split
        in two is two journeys. coordinator.everyone() is that join.
        """
        return [face for face in self.coordinator.everyone()
                if face.get("prowling")]

    @property
    def is_on(self) -> bool:
        return bool(self._circling())

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "faces": [
                {"face_id": face["id"],
                 # The name where there is one. An unnamed circuit is the more
                 # interesting case, and reads as the id rather than nothing.
                 "name": face.get("name"),
                 "cameras": face.get("cameras", [])}
                for face in self._circling()
            ],
        }


class H500MediaProblem(CoordinatorEntity[H500Coordinator], BinarySensorEntity):
    """On when the hub has stopped serving recordings.

    The sentinel's verdict as an entity, because a repair notice tells a
    human and a binary sensor lets an automation act -- send the phone a
    message, power-cycle the hub's smart plug. On means the known wedge
    (port 8800 accepting and closing without a byte); off means the
    unauthenticated handshake answered; unknown before the first check.
    "unreachable" and "silent" stay off -- the failing poll already covers
    the first and the second is one slow reply from crying wolf -- but the
    raw verdict rides along as an attribute for automations that care.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "media_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_media_problem"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        # The same question the wedge clock asks, asked once. Two copies of
        # "is it wedged" would drift, and the clock's whole value is that its
        # resets line up with this sensor's edges.
        if self.coordinator.media_status is None and not getattr(
                self.coordinator, "media_serving_empty", False):
            # Nothing has been checked and nothing has been downloaded: no
            # verdict is available, which is not the same as "fine".
            return None
        return self.coordinator.media_wedged

    @property
    def extra_state_attributes(self) -> dict:
        return {"media_status": self.coordinator.media_status,
                "serving_empty":
                bool(getattr(self.coordinator, "media_serving_empty", False))}


class H500CamerasDark(CoordinatorEntity[H500Coordinator], BinarySensorEntity):
    """On when every camera has stopped recording at the same time.

    One quiet camera is a quiet back gate, which is why its own sensor is
    adjustable and why it is not an alarm on its own. Every camera quiet at
    once, on a hub still answering every poll, is a different fact -- and it
    is the failure this project exists around. Some hours after the hub
    restarts the cameras go dark: they keep their radio link, they still
    answer live view, and they record nothing. The app shows them connected.

    There is nothing to read that says otherwise. The hub's paired-device
    record has sixteen fields and not one is an online flag, a signal strength
    or a battery, and the eleven battery methods all answer -40106. The
    simultaneity is the entire signal, which is why it needs its own entity
    rather than a glance across several.

    Unavailable rather than off while the hub is not answering: a failing poll
    is the hub's problem, not the cameras'.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "cameras_dark"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_cameras_dark"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        cameras = self.coordinator.cameras
        if not cameras:
            return None
        verdicts = [camera_is_silent(self.coordinator, index)
                    for index in range(len(cameras))]
        if any(verdict is None for verdict in verdicts):
            return None
        return all(verdicts)

    @property
    def extra_state_attributes(self) -> dict:
        coordinator = self.coordinator
        gaps = [seconds for index in range(len(coordinator.cameras))
                if (seconds := coordinator.silent_seconds(index)) is not None]
        return {
            "cameras": len(coordinator.cameras),
            # The shortest gap: how long ago the last of them stopped, which
            # is when the outage began rather than when the quietest one did.
            "dark_for_hours": round(min(gaps) / 3600, 1) if gaps else None,
            # Whether the hub's own media path went at the same moment. Both
            # failing is one hub problem; this alone is the cameras, and
            # telling them apart is the point of having this at all.
            "media_status": coordinator.media_status,
        }


class H500FaceSeenRecently(CoordinatorEntity[H500Coordinator], BinarySensorEntity):
    """Whether this person was seen in the last few minutes.

    Named "seen recently" rather than "home", and deliberately not a
    device_tracker, because the honest claim is much weaker than presence. A
    camera watches a doorstep, not a house: someone indoors is invisible to
    it, and so is someone who left through a door with no camera on it. Off
    means "not seen", which is not the same as "away", and building an
    occupancy automation on it would be building on a guess.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "face_present"

    def __init__(self, coordinator, entry, face_id: str) -> None:
        super().__init__(coordinator)
        self.face_id = str(face_id)
        self._attr_unique_id = f"{entry.entry_id}_face_{self.face_id}_recent"
        self._attr_device_info = hub_device(coordinator, entry)

    @property
    def name(self) -> str:
        who = self.coordinator.face_names.get(self.face_id) \
            or f"Face {self.face_id}"
        return f"{who} seen recently"

    @property
    def is_on(self) -> bool:
        from homeassistant.util import dt as dt_util
        # Merged across every cluster: seen on either is seen.
        last = self.coordinator.person_for(self.face_id).get("last_seen")
        if last is None:
            return False
        now = int(dt_util.utcnow().timestamp())
        return (now - last) <= FACE_PRESENCE_WINDOW
