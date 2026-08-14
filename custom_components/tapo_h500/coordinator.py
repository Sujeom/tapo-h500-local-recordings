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
    attach_detections, combine_visits, describe_codes, detection_types,
    direction, end_of, event_type, face_ids, has_detection, local_date,
    merge_visits, newest_matching, prowling, same_encounter, sessions,
    start_of, suggest_ranks,
)
from .const import (
    AUTO_DOWNLOAD_ALL, AUTO_DOWNLOAD_RINGS, CONF_AUTO_DOWNLOAD, CONF_CONVERT_MP4,
    CONF_KEEP_DOWNLOADS, CONF_POLL_INTERVAL, DEFAULT_AUTO_DOWNLOAD,
    DEFAULT_CONVERT_MP4, DEFAULT_KEEP_DOWNLOADS,
    CAMERAS_MAX_AGE, CONF_FACE_NAMES, CONF_KEEP_RINGS, DEFAULT_KEEP_RINGS,
    CONF_CAMERA_ORDER, CONF_KEEP_PERSON, DEFAULT_KEEP_PERSON, PERSON_CODES,
    CONF_DOWNLOAD_TYPES,
    CONF_SENSITIVITY, DEFAULT_SENSITIVITY, SENSITIVITY_LEVELS,
    DIRECTION_WINDOW, FACE_TRAIL_MAX, DEFAULT_POLL_INTERVAL, DOMAIN, EVENT_ARRIVAL, EVENT_RING,
    ENCOUNTER_SECONDS, EVENT_VISIT, LOITER_GAP,
    LOOKBACK_SECONDS, POLL_BACKOFF_MAX, PROWL_WINDOW, SIGNAL_NEW_CLIP,
    STATUS_MAX_AGE, STORAGE_SAMPLES,
)
from .media import (
    async_download_clip, async_prune, async_verify, existing_clip,
)
from .status import hub_readings, hours_until_full, trend_samples

_LOGGER = logging.getLogger(__name__)


def interval_or_default(entry: ConfigEntry) -> int:
    return entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)


def backoff_seconds(base: int, failures: int, cap: int) -> int:
    """How long to wait after this many consecutive failures.

    Doubling, capped. Deliberately not jittered: there is one hub and one
    poller, so there is no thundering herd to spread out, and a predictable
    interval is easier to recognise in a log than a random one.
    """
    if failures <= 0:
        return base
    return min(cap, base * (2 ** failures))


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
        self._failures = 0
        # Who has already been seen today, and which day that refers to.
        self._arrived: set[str] = set()
        self._arrival_day: str | None = None
        # The start of the newest visit already announced, per camera...
        self._visits: dict[int, int] = {}
        # ...and the visits announced recently, whatever camera they came from,
        # so a second camera seeing the same arrival on the next poll does not
        # announce it again.
        self._encounters: list[dict] = []
        # How full the disk has been, so a rate can be fitted to it. Memory
        # only: the hub reports how full it is now and nothing about before.
        self.storage_trend: list[tuple[int, float]] = []
        # When the notification snooze ends. None is not snoozed; infinity is
        # snoozed until somebody says otherwise.
        self.snoozed_until: float | None = None
        self._base_interval = interval_or_default(entry)
        # Turn "refresh this every N seconds" into a poll count once, here, so
        # a longer interval configured in options does not silently turn into
        # a much longer refresh age.
        interval = self._base_interval
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

    @property
    def camera_ranks(self) -> dict[str, int]:
        """Camera name to its distance from the street, where set.

        Keyed by name rather than index because a trail records names, and
        because the paired-list index shifts if a camera is removed while a
        name does not.
        """
        stored = self.entry.options.get(CONF_CAMERA_ORDER) or {}
        ranks: dict[str, int] = {}
        for key, value in stored.items():
            try:
                ranks[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return ranks

    def sensitivity(self, index: int) -> tuple[float, int]:
        """How far above its own average this camera has to be to stand out.

        Keyed by camera name rather than index, the same way the layout is: an
        index shifts when a camera is unpaired and a name does not.

        Falls back to the level that has always been used, so a hub configured
        before this existed behaves exactly as it did -- and so does one whose
        stored level is a word this version has never heard of.
        """
        stored = self.entry.options.get(CONF_SENSITIVITY) or {}
        name = (self.cameras[index].get("alias")
                if index < len(self.cameras) else None)
        level = stored.get(name, DEFAULT_SENSITIVITY)
        return SENSITIVITY_LEVELS.get(
            level, SENSITIVITY_LEVELS[DEFAULT_SENSITIVITY])

    def faces_seen(self, index: int | None = None,
                   clips: dict[int, list[dict]] | None = None) -> dict[str, dict]:
        """Every face in the current window, newest sighting first.

        Mirrors the cards' own grouping so a sensor and a card never disagree
        about how many times someone has been seen.

        `clips` overrides what the last completed poll stored, which matters
        exactly once: the arrival check runs inside the poll that fetched the
        recordings, before the coordinator has published them. Reading the
        published copy there would work off the previous poll's data, and on
        the second poll after a restart that turns everyone seen earlier
        today into a fresh arrival.
        """
        indexes = range(len(self.cameras)) if index is None else [index]
        source = clips if clips is not None else (self.data or {}).get("clips", {})
        faces: dict[str, dict] = {}
        for position in indexes:
            for clip in source.get(position, []):
                for face_id in face_ids(clip):
                    key = str(face_id)
                    seen = faces.setdefault(
                        key, {"id": key, "sightings": 0, "last_seen": None,
                              "first_seen": None,
                              "camera_index": None, "cameras": set(),
                              "trail": []})
                    seen["sightings"] += 1
                    moment = start_of(clip)
                    # The oldest sighting still inside the poll window. Not a
                    # lifetime first: the window is a day, and anything older
                    # than that the hub no longer returns.
                    if moment is not None and (seen["first_seen"] is None
                                               or moment < seen["first_seen"]):
                        seen["first_seen"] = moment
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
                    if moment is not None:
                        seen["trail"].append(
                            {"camera": alias or f"Camera {position}",
                             "at": moment})
        names = self.face_names
        for key, face in faces.items():
            face["name"] = names.get(key)
            face["cameras"] = sorted(face["cameras"])
            # Newest first, and capped: this becomes an entity attribute and
            # is rewritten to the state machine on every update.
            face["trail"] = sorted(
                face["trail"], key=lambda hop: hop["at"],
                reverse=True)[:FACE_TRAIL_MAX]
            # Where they are now, as far as the hub knows.
            face["last_camera"] = (face["trail"][0]["camera"]
                                   if face["trail"] else None)
            # ...and which way they were going, when that is actually known.
            face["direction"] = direction(
                face["trail"], self.camera_ranks, DIRECTION_WINDOW)
            # ...and whether that trail is a circuit rather than a journey.
            # Needs no ranks, so it works before anyone fills in the layout.
            face["prowling"] = prowling(face["trail"], PROWL_WINDOW)
        return faces

    @property
    def named_people(self) -> dict[str, list[str]]:
        """Every name that has been given, and the face ids sharing it.

        Built from the stored names alone rather than from what has been seen,
        so somebody who was away all day still has their entities.
        """
        groups: dict[str, list[str]] = {}
        for face_id, name in self.face_names.items():
            groups.setdefault(name, []).append(face_id)
        return {name: sorted(ids) for name, ids in sorted(groups.items())}

    def people(self, clips: dict[int, list[dict]] | None = None) -> dict[str, dict]:
        """Named faces merged into one record per person, keyed by name.

        The hub clusters faces, and it clusters the same person more than once
        -- different light, a hat, a different angle -- handing out a separate
        id for each. Naming both is the only way to say they are one person,
        and until now nothing downstream believed it: two sensors called
        "Alice", two arrival events, a trail split in half so the direction she
        was walking could not be worked out from either half.

        Everything is recomputed from the merged trail rather than picked from
        one of the parts. That is the whole point -- gate on one id and door on
        the other is a direction only once they are the same person.
        """
        merged: dict[str, dict] = {}
        groups = self.named_people
        for face_id, face in self.faces_seen(clips=clips).items():
            name = face.get("name")
            if not name:
                continue
            person = merged.setdefault(name, {
                "name": name, "ids": [], "sightings": 0, "first_seen": None,
                "last_seen": None, "camera_index": None, "face_id": None,
                "cameras": set(), "trail": [],
            })
            # Every cluster carrying this name, not only the ones seen in the
            # window. An automation matching on the ids it was handed has to
            # match this person tomorrow too, when a different cluster of
            # theirs is the one the hub recognises.
            person["ids"] = groups.get(name) or [face_id]
            person["sightings"] += face.get("sightings", 0)
            first, last = face.get("first_seen"), face.get("last_seen")
            if first is not None and (person["first_seen"] is None
                                      or first < person["first_seen"]):
                person["first_seen"] = first
            if last is not None and (person["last_seen"] is None
                                     or last > person["last_seen"]):
                person["last_seen"] = last
                person["camera_index"] = face.get("camera_index")
                # Which cluster saw them last, so a caller can still find that
                # sighting's own photograph.
                person["face_id"] = face_id
            person["cameras"].update(face.get("cameras") or [])
            person["trail"].extend(face.get("trail") or [])
        for person in merged.values():
            # The entity that represents this person is keyed on the lowest id,
            # so a person the hub only ever clustered once keeps exactly the
            # unique id they already had.
            person["id"] = person["ids"][0]
            person["cameras"] = sorted(person["cameras"])
            person["trail"] = sorted(
                person["trail"], key=lambda hop: hop["at"],
                reverse=True)[:FACE_TRAIL_MAX]
            person["last_camera"] = (person["trail"][0]["camera"]
                                     if person["trail"] else None)
            person["direction"] = direction(
                person["trail"], self.camera_ranks, DIRECTION_WINDOW)
            person["prowling"] = prowling(person["trail"], PROWL_WINDOW)
        return merged

    def everyone(self) -> list[dict]:
        """One record per distinct person in the window.

        Named faces merged, and every face with no name as itself. Unnamed
        clusters cannot be merged -- a name is the only evidence that two ids
        are one person -- and they are the more interesting half anyway.
        """
        return list(self.people().values()) + [
            face for face in self.faces_seen().values() if not face.get("name")]

    def suggested_ranks(self) -> dict[str, int]:
        """Where the cameras probably sit, from how people have actually moved.

        The layout screen calls this the one thing the integration cannot work
        out for itself, which was true of the hub and not of the recordings:
        people arrive from the street and walk towards the door, so whichever
        camera sees them first is the one nearer the street.

        A suggestion and nothing more -- it fills in the form's defaults and
        the owner still presses submit. Empty until somebody has actually been
        seen crossing between two cameras.
        """
        return suggest_ranks([person.get("trail") or []
                              for person in self.everyone()], DIRECTION_WINDOW)

    def person_for(self, face_id: str) -> dict:
        """The merged person an id belongs to, or the bare face if unnamed."""
        key = str(face_id)
        name = self.face_names.get(key)
        if name:
            return self.people().get(name) or {}
        return self.faces_seen().get(key) or {}

    def _note_arrivals(self, clips: dict[int, list[dict]]) -> None:
        """Fire once per named PERSON per local day, on their first sighting.

        Per person, not per face id. The hub clusters the same person more than
        once, and keying this on the cluster announced Alice twice on any
        morning both of hers happened to fire -- which reads as her arriving,
        leaving and arriving again.

        The detection event fires every time anyone crosses a camera. That is
        correct for a doorbell and useless for a household: someone who works
        from home trips the front camera a dozen times a day, and only the
        first of those is worth being told about. This is the only place that
        distinction exists.

        The set is rebuilt when the local day rolls over rather than at a
        fixed hour, so it follows whatever "today" means here.

        Silent on the first poll. The window holds a day of recordings, so a
        restart at teatime would otherwise announce everyone who came home
        that morning as if they had just walked in.
        """
        now = int(dt_util.utcnow().timestamp())
        today = local_date(now)
        if self._arrival_day != today:
            self._arrival_day = today
            self._arrived = set()
        # people() holds only faces that have been named, which is the filter
        # this used to apply by hand: an unnamed id is a stranger, and a
        # stranger appearing is what the detection event already reports.
        # "Face 481036337152 has arrived" would be worse than saying nothing.
        for name, person in self.people(clips=clips).items():
            if name in self._arrived:
                continue
            last = person.get("last_seen")
            if last is None or local_date(last) != today:
                continue
            self._arrived.add(name)
            if not self._primed:
                continue
            self.hass.bus.async_fire(EVENT_ARRIVAL, {
                "entry_id": self.entry.entry_id,
                # The cluster that actually saw them, so a caller can still
                # find that sighting's photograph...
                "face_id": person.get("face_id"),
                # ...and every cluster that is this person, because matching
                # on one id would miss half their sightings.
                "face_ids": person.get("ids") or [],
                "name": name,
                "camera": person.get("last_camera"),
                "at": last,
                # Where they were heading, when the cameras have been given an
                # order. Absent otherwise rather than guessed.
                "direction": person.get("direction"),
            })

    def _visit_payload(self, index: int, clips: list[dict],
                       visit: tuple[int, int, int]) -> dict:
        """What one visit looks like to an automation."""
        start, end, count = visit
        during = [clip for clip in clips
                  if start <= (start_of(clip) or -1) <= end]
        codes = sorted({code for clip in during
                        for code in detection_types(clip)})
        names = self.face_names
        faces = sorted({str(face) for clip in during
                        for face in face_ids(clip)})
        camera = (self.cameras[index] if index < len(self.cameras) else {})
        return {
            "entry_id": self.entry.entry_id,
            "camera": camera.get("alias") or f"Camera {index}",
            "camera_index": index,
            "at": start,
            # How many recordings the visit has so far, which at the moment it
            # is announced is one. It grows; the event does not fire again.
            "recordings": count,
            "detections": codes,
            "detection": describe_codes(codes),
            "face_ids": faces,
            # Who, where they have been named. An empty list is the ordinary
            # case and means "nobody the hub matched to a name", not "nobody".
            "names": sorted({names[face] for face in faces if face in names}),
        }

    def _note_visits(self, clips_by_camera: dict[int, list[dict]]) -> None:
        """Fire once when a visit begins, rather than once per recording.

        The detection event is the right grain for a doorbell press and the
        wrong one for a person: the hub reports moments, so four minutes at the
        door is a string of fifteen-second clips and an automation wired to
        detections sends sixteen notifications about one visitor.

        Announced at the start of the visit, because that is the only moment a
        notification is worth sending. Nothing about how it ends is known then
        -- everybody who is about to stay ten minutes has also been there for
        one clip -- which is why the delivery and loitering signals exist
        separately and are both retrospective.

        Silent until primed, like arrivals and for the same reason: the window
        holds a day, so a restart would otherwise announce every visit since
        breakfast at once.
        """
        fresh: list[dict] = []
        for index, clips in clips_by_camera.items():
            visits = sessions(clips, LOITER_GAP)
            if not visits:
                continue
            newest = visits[-1]
            # Only ever forward. A clip arriving late cannot resurrect a visit
            # already announced, and an earlier one appearing in the window is
            # history rather than news.
            if newest[0] <= self._visits.get(index, 0):
                continue
            self._visits[index] = newest[0]
            if not self._primed:
                continue
            fresh.append(self._visit_payload(index, clips, newest))

        for group in merge_visits(fresh, ENCOUNTER_SECONDS, DIRECTION_WINDOW):
            combined = combine_visits(group)
            # Cameras rarely index a shared arrival on the same poll -- at two
            # seconds apart they usually land on consecutive ones -- so the
            # merge above catches only half the cases. The other half is
            # answered by remembering what was just announced.
            if any(same_encounter(before, combined, ENCOUNTER_SECONDS,
                                  DIRECTION_WINDOW)
                   for before in self._encounters):
                continue
            self._encounters.append(combined)
            self.hass.bus.async_fire(EVENT_VISIT, combined)
        # Nothing older than the longest window either rule looks at can
        # suppress anything, and this is memory that would otherwise only grow.
        cutoff = int(dt_util.utcnow().timestamp()) - DIRECTION_WINDOW
        self._encounters = [entry for entry in self._encounters
                            if entry["at"] >= cutoff]

    def clips_for(self, index: int) -> list[dict]:
        return (self.data or {}).get("clips", {}).get(index, [])

    def last_activity(self, index: int) -> int | None:
        moments = [start_of(clip) for clip in self.clips_for(index)]
        moments = [moment for moment in moments if moment is not None]
        return max(moments) if moments else None

    @property
    def snoozed(self) -> bool:
        """Whether notifications are muted right now.

        A flag, not a filter. Nothing here stops recording, downloading or
        firing events while it is set -- footage during a snooze is the
        footage most likely to be wanted afterwards. What it mutes is the
        automation, which is where notifications are decided.

        Expiry needs no timer. Every entity reading this redraws on each poll,
        which is every couple of seconds.
        """
        if self.snoozed_until is None:
            return False
        if dt_util.utcnow().timestamp() >= self.snoozed_until:
            # Tidy up so the attribute does not keep showing a past time.
            self.snoozed_until = None
            return False
        return True

    def snooze(self, seconds: float | None) -> float | None:
        """Mute for this long. None is indefinite, 0 cancels.

        Deliberately not written to disk. A snooze is a "not for the next
        hour" decision, and one that outlived a restart would be a silent
        doorbell nobody remembered turning off.
        """
        if seconds is not None and seconds <= 0:
            self.snoozed_until = None
        elif seconds is None:
            self.snoozed_until = float("inf")
        else:
            self.snoozed_until = dt_util.utcnow().timestamp() + seconds
        self.async_update_listeners()
        return self.snoozed_until

    def days_until_full(self) -> float | None:
        """When the hub starts overwriting, at the rate seen so far."""
        hours = hours_until_full(
            self.storage_trend, self.readings.get("storage_used_percent"))
        return None if hours is None else round(hours / 24, 2)

    def silent_seconds(self, index: int) -> int | None:
        """How long this camera has produced nothing, as far as can be told.

        None until the first poll has completed -- before that, an empty list
        means "not asked yet", not "nothing happened", and reporting every
        camera silent on startup would be an alarm about the integration
        rather than the hardware.

        Capped at the poll window. A camera that has produced nothing at all
        is reported as exactly the window, because that is the whole of what
        the hub was asked about; anything longer would be invented.
        """
        if not self._primed:
            return None
        last = self.last_activity(index)
        if last is None:
            return LOOKBACK_SECONDS
        return max(0, int(dt_util.utcnow().timestamp()) - last)

    def silent_cameras(self, threshold: int) -> list[str]:
        """The names of every camera quiet for longer than `threshold`."""
        quiet = []
        for index, camera in enumerate(self.cameras):
            seconds = self.silent_seconds(index)
            if seconds is not None and seconds >= threshold:
                quiet.append(camera.get("alias") or f"Camera {index}")
        return quiet

    async def _async_update_data(self) -> dict:
        """Poll, and back off while the hub is not answering."""
        try:
            data = await self._poll()
        except Exception:
            self._failures += 1
            delay = backoff_seconds(
                self._base_interval, self._failures, POLL_BACKOFF_MAX)
            if delay != (self.update_interval.total_seconds()
                         if self.update_interval else None):
                _LOGGER.debug("Hub not answering; polling every %ss", delay)
                self.update_interval = timedelta(seconds=delay)
            raise
        if self._failures:
            _LOGGER.debug("Hub answering again; back to every %ss",
                          self._base_interval)
            self._failures = 0
            self.update_interval = timedelta(seconds=self._base_interval)
        return data

    async def _poll(self) -> dict:
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
                # One sample per status refresh, which is once a minute. The
                # forecast has nothing else to work from -- the hub reports
                # how full it is and never how full it was.
                self.storage_trend = trend_samples(
                    self.storage_trend, now,
                    self.readings.get("storage_used_percent"),
                    STORAGE_SAMPLES)
            except Exception as err:
                # Status is a bonus; never fail the whole poll over it.
                _LOGGER.debug("Hub status unavailable: %s", err)
        # Before _primed is set, so a restart mid-afternoon records everyone
        # already seen today without announcing them again.
        try:
            self._note_arrivals(clips_by_camera)
        except Exception as err:  # noqa: BLE001 - never fail a poll over this
            _LOGGER.debug("Could not check arrivals: %s", err)
        try:
            self._note_visits(clips_by_camera)
        except Exception as err:  # noqa: BLE001 - never fail a poll over this
            _LOGGER.debug("Could not check visits: %s", err)
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

    @property
    def download_types(self) -> set[int]:
        """Which detections are worth the disk. An empty set means all of them.

        Read live rather than being listed in RELOAD_ON_CHANGE, deliberately.
        Nothing about the hub connection changes when this does, and a reload
        costs a fresh login to a device that wedges under repeated ones.
        """
        codes: set[int] = set()
        for value in self.entry.options.get(CONF_DOWNLOAD_TYPES) or []:
            try:
                codes.add(int(value))
            except (TypeError, ValueError):
                continue
        return codes

    def _download_new(self, index, camera, clips, window) -> None:
        mode = self.entry.options.get(CONF_AUTO_DOWNLOAD, DEFAULT_AUTO_DOWNLOAD)
        wanted = self.download_types
        for clip in self._fresh(index, clips, self._seen_clips, window):
            if not self._primed or mode not in (AUTO_DOWNLOAD_ALL, AUTO_DOWNLOAD_RINGS):
                continue
            if mode == AUTO_DOWNLOAD_RINGS and event_type(clip) != EVENT_RING:
                continue
            # Nothing chosen means no filter, which is what every installation
            # made before this existed has -- and what it keeps.
            if wanted and not has_detection(clip, wanted):
                continue
            self.entry.async_create_background_task(
                self.hass, self._download(index, camera, clip),
                f"{DOMAIN} download {start_of(clip)}",
            )

    def _protected(self, index: int) -> set[int]:
        """Clip start times retention must leave alone, however old they get.

        Two classes, each with its own count, because they are the two people
        actually go back for: somebody at the door, and somebody there at all.
        A single retention number let a busy afternoon of motion evict the
        press that was the whole reason for keeping anything.

        Nine detection codes exist and only these two get a number. The rest
        would be seven more boxes on a form saying the same thing -- motion is
        a cat, vehicles are the road, and the face codes never fire without
        the person code beside them.
        """
        options = self.entry.options
        clips = self.clips_for(index)
        return newest_matching(
            clips, lambda clip: event_type(clip) == EVENT_RING,
            options.get(CONF_KEEP_RINGS, DEFAULT_KEEP_RINGS),
        ) | newest_matching(
            clips, lambda clip: has_detection(clip, PERSON_CODES),
            options.get(CONF_KEEP_PERSON, DEFAULT_KEEP_PERSON),
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
        # Verified now, while the hub still holds the original. A truncated
        # file looks identical to a good one on disk, and the only moment it
        # can be fetched again is before retention evicts the source.
        stored = existing_clip(self.hass, camera, start_time)
        if stored is not None and not await async_verify(self.hass, stored):
            _LOGGER.warning(
                "Downloaded clip %s does not decode; removing it so it can be "
                "fetched again while the hub still has it", stored.name)
            await self.hass.async_add_executor_job(stored.unlink, True)
            self._seen_clips.get(index, set()).discard((start_time,))
            return
        # Only automatic downloads are pruned. A manual download is a
        # deliberate choice and is left alone.
        keep = self.entry.options.get(CONF_KEEP_DOWNLOADS, DEFAULT_KEEP_DOWNLOADS)
        for removed in await async_prune(self.hass, camera, keep,
                                         self._protected(index)):
            _LOGGER.debug("Pruned %s to keep the newest %s", removed, keep)
        async_dispatcher_send(self.hass, self.signal("image", index))
