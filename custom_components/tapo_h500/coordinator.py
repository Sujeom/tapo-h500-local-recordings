"""Polls the hub, turns new activity into events, and downloads rings."""
from __future__ import annotations

import asyncio
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
    direction, end_of, event_type, face_ids, has_detection, in_night,
    local_date, local_hour, merge_visits, newest_matching, prowling,
    same_encounter, sessions, start_of, suggest_ranks,
)
from .const import (
    AUTO_DOWNLOAD_ALL, AUTO_DOWNLOAD_RINGS, CONF_AUTO_DOWNLOAD, CONF_CONVERT_MP4,
    CONF_KEEP_DOWNLOADS, CONF_POLL_INTERVAL, DEFAULT_AUTO_DOWNLOAD,
    DEFAULT_CONVERT_MP4, DEFAULT_KEEP_DOWNLOADS,
    CAMERAS_MAX_AGE, CONF_FACE_NAMES, CONF_KEEP_RINGS, DEFAULT_KEEP_RINGS,
    CONF_CAMERA_ORDER, CONF_KEEP_PERSON, DEFAULT_KEEP_PERSON, PERSON_CODES,
    CONF_DOWNLOAD_TYPES, CONF_NIGHT_END, CONF_NIGHT_START,
    DEFAULT_NIGHT_END, DEFAULT_NIGHT_START,
    CONF_SENSITIVITY, DEFAULT_SENSITIVITY, SENSITIVITY_LEVELS,
    DIRECTION_WINDOW, DOWNLOAD_RETRY_LIMIT, FACE_TRAIL_MAX, DEFAULT_POLL_INTERVAL, DOMAIN, EVENT_ARRIVAL, EVENT_RING,
    ENCOUNTER_SECONDS, EVENT_VISIT, LOITER_GAP,
    AUTO_RESTART_COOLDOWN, AUTO_RESTART_CURE_WINDOW, AUTO_RESTART_RECHECK,
    CONF_AUTO_RESTART,
    DEFAULT_AUTO_RESTART,
    EVENT_AUTO_RESTART,
    FIRMWARE_CHECK_SECONDS, LOOKBACK_SECONDS, MEDIA_CHECK_SECONDS,
    MEDIA_EVIDENCE_MAX_AGE,
    POLL_BACKOFF_MAX, POLL_IDLE_AFTER, POLL_IDLE_INTERVAL, PROWL_WINDOW,
    WEDGE_HISTORY_SECONDS,
    SIGNAL_NEW_CLIP,
    STATUS_MAX_AGE, STORAGE_SAMPLES, TAMPER_CODES,
)
from .media import (
    EmptyRecordingError, async_download_clip, async_latest_image,
    async_preview_clip, async_prune, async_prune_previews, async_verify,
    existing_clip,
)
from .status import hub_readings, hours_until_full, trend_samples

_LOGGER = logging.getLogger(__name__)

# Seconds between the deep check's first empty answer and its confirming
# second fetch. Module-level so tests can zero it.
EMPTY_CONFIRM_DELAY = 10


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
        # Per camera, the newest clip start a frame fetch was begun for and
        # the task doing it; see async_latest_frame.
        self._frame_attempts: dict[int, tuple[int, asyncio.Task]] = {}
        # One scan of the window per poll, shared by every entity that asks.
        # The source each answer was computed from, so a new poll's clips
        # invalidate it by being a different object.
        self._faces_source: object = None
        self._faces_names: dict[str, str] = {}
        self._faces_cache: dict[int | None, dict] = {}
        self._people_source: object = None
        self._people_names: dict[str, str] = {}
        self._people_cache: dict | None = None
        self._primed = False
        # When anything new was last noticed, for the idle backoff. Started at
        # now rather than at zero, so a restart runs at full speed for the
        # first ten minutes instead of treating a fresh process as a quiet
        # house.
        self._last_activity_at = dt_util.utcnow().timestamp()
        self._polls = 0
        # Set by a write so the next poll reads status even when the modulo
        # would skip it; at a 2s interval status is every 30th poll, so
        # without this a control snaps back to its old value for a minute.
        self._force_status = False
        # Which cameras are currently held as silent, and the activity stamp
        # they were holding at. Only a newer recording clears one.
        self._silent_latched: dict[int, int | None] = {}
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
        self._media_every = max(1, round(MEDIA_CHECK_SECONDS / interval))
        self._firmware_every = max(
            1, round(FIRMWARE_CHECK_SECONDS / interval))
        # What the last cloud firmware check said; the update entity reads it.
        self.firmware_info: dict = {}
        # What the last media-port handshake said: "healthy", "wedged",
        # "silent", "unreachable", or None before the first check. The wedge
        # is the hub's known failure mode and this is how it is noticed
        # before anyone misses a photograph; repairs.py reads it.
        self.media_status: str | None = None
        # The hub's last status answer as it arrived, unparsed.
        self.raw_status: dict = {}
        # Whether this wedge episode has already had its one player_id
        # rotation; see the case-D note where it happens.
        self._wedge_rotated = False
        # Consecutive automatic-download failures per camera index, reset by
        # any success. Three in a row is a pattern -- ffmpeg missing, disk
        # full, media service refusing -- and repairs.py turns it into a
        # notice instead of a warning in a log nobody reads.
        self._download_failures: dict[int, int] = {}
        # Clips that did not download, and how many times each was tried.
        # Retried when the hub recovers, not on the next poll -- see
        # _remember_failed_clip.
        self._failed_clips: dict[int, dict[int, int]] = {}
        # Whether the repair checks are currently failing, so the warning is
        # said once rather than every two seconds.
        self._repairs_broken = False
        # Consecutive downloads that completed cleanly with zero video --
        # the hub's second media failure (2026-08-18): every session works
        # and carries nothing, for every clip, until a reboot. Hub state,
        # so one counter, not per camera; the handshake sentinel cannot see
        # it, so the downloads are the evidence.
        self._empty_downloads = 0
        # When the last automatic restart happened, if the owner opted in
        # -- and whether one failed to cure, which stops further attempts
        # until real recovery re-arms them.
        self._auto_restarted = 0.0
        self._auto_restart_broken = False
        # When to force the deep check after an automatic restart, so the
        # cure is verified rather than assumed. None means nothing pending.
        self._recheck_at: float | None = None
        # When a media session last taught us anything -- bytes served or a
        # confirmed empty answer. Stale evidence plus an indexed clip is
        # what triggers the deep check.
        self._media_evidence = 0.0
        # The wedge record. One entry per outage: when it started, what was
        # tried, and when it ended. The hub keeps no such history, and by the
        # time anybody asks how often this happens -- or whether restarting
        # ever actually helped -- the evidence is a month of anecdote.
        self.wedges: list[dict] = []
        self._healthy_since = dt_util.utcnow().timestamp()
        self._longest_healthy = 0.0
        self._was_wedged = False

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
        today into a fresh arrival. That path is never cached: it is a
        different question about different data.

        Held for the poll it was computed from. Every face sensor, every
        person sensor and the household count ask this, so one scan of every
        clip on every camera was happening dozens of times for one poll's
        worth of unchanged recordings. Two cameras is a millisecond and
        nobody would notice; sixteen busy ones is a third of a second, which
        is a sixth of the poll interval spent answering the same question.

        The answer is shared, not copied. Callers read it; a caller that
        started editing it would be editing everybody's.
        """
        if clips is not None:
            return self._scan_faces(index, clips)
        source = (self.data or {}).get("clips", {})
        # Identity for the clips, because each poll builds a new dictionary
        # and the cache holds a reference to the one it answered for, so the
        # object cannot be recycled underneath this. Equality for the names,
        # because naming a face changes the answer without changing the
        # recordings and does not reload the entry -- a reload costs a fresh
        # login to a hub that wedges under repeated ones. Without this a name
        # somebody just typed would not appear until the next poll.
        names = self.face_names
        if self._faces_source is not source or self._faces_names != names:
            self._faces_source = source
            self._faces_names = dict(names)
            self._faces_cache = {}
        if index not in self._faces_cache:
            self._faces_cache[index] = self._scan_faces(index, source)
        return self._faces_cache[index]

    def _scan_faces(self, index: int | None, source: dict) -> dict[str, dict]:
        indexes = range(len(self.cameras)) if index is None else [index]
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

        Held for the poll it was computed from, like the faces it merges, and
        shared rather than copied for the same reason.
        """
        if clips is not None:
            return self._merge_people(clips)
        source = (self.data or {}).get("clips", {})
        names = self.face_names
        if self._people_source is not source or self._people_names != names:
            self._people_source = source
            self._people_names = dict(names)
            self._people_cache = None
        if self._people_cache is None:
            self._people_cache = self._merge_people(None)
        return self._people_cache

    def _merge_people(self, clips: dict | None) -> dict[str, dict]:
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

    def household(self, window: int) -> dict:
        """Who has been seen lately, who has been seen today, and who has not.

        One entity per person is the right shape for automating and the wrong
        one for looking at: with five people named, "is anybody about" means
        reading five sensors and comparing five timestamps by eye.

        Deliberately three lists rather than a presence guess. `not_seen`
        especially: a camera watches a doorstep, not a house, so somebody
        indoors is invisible to it and somebody who left through a door with
        no camera on it is too. Not being seen is not evidence of absence, and
        the names say so.
        """
        now = int(dt_util.utcnow().timestamp())
        today = local_date(now)
        people = self.people()
        recent: list[str] = []
        so_far: list[str] = []
        for name in self.named_people:
            last = (people.get(name) or {}).get("last_seen")
            if last is None:
                continue
            if now - last <= window:
                recent.append(name)
            if local_date(last) == today:
                so_far.append(name)
        return {
            "seen_recently": sorted(recent),
            "seen_today": sorted(so_far),
            "not_seen": sorted(set(self.named_people) - set(recent)),
        }

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
        options = self.entry.options
        return {
            "entry_id": self.entry.entry_id,
            "camera": camera.get("alias") or f"Camera {index}",
            "camera_index": index,
            "at": start,
            # Decided here rather than in whatever reads this. A window that
            # wraps midnight is the obvious thing to get wrong -- 23 is inside
            # 22-to-6 and 12 is not, and comparing naively marks the whole day
            # as night -- and every consumer would get it wrong separately.
            "night": in_night(
                local_hour(start),
                options.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
                options.get(CONF_NIGHT_END, DEFAULT_NIGHT_END)),
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

    async def async_latest_frame(self, index: int, camera: dict) -> bytes | None:
        """The newest indexed clip's frame, fetched from the hub if need be.

        The camera and latest-event entities promise the newest clip's frame,
        but a thumbnail is written by a download. So whenever the newest clip
        is not downloaded yet -- still in flight, filtered out by mode or
        download type, or failed -- the newest frame on disk is the previous
        event, which is exactly what the notification's Camera button used to
        show: an old photo, at the moment somebody pressed it to see this one.

        The preview machinery makes exactly the missing file and caches it at
        the path the download would use, so one fetch per clip closes the gap.
        One, marked before it starts: this runs on every frontend look at the
        picture, and a hub that would not serve the frame the first time must
        not be asked once per poll -- each ask is a whole media session
        against a device that is easy to overload. The next clip gets its own
        attempt, so a refusal does not stick.

        Marking alone is not enough, because the frontend asks the camera and
        the latest-event picture at the same moment. The second one would find
        the clip already marked, skip the fetch and read the file while the
        first was still writing it -- served the old frame by the very
        bookkeeping meant to stop it. So the fetch is shared: whoever arrives
        during one waits for it and sees what it produced.

        While the hub is still recording there is no indexed clip and no
        frame of it exists anywhere, so nothing is asked for and the previous
        event is served -- that part is physics.
        """
        starts = [start for clip in self.clips_for(index)
                  if (start := start_of(clip)) is not None
                  and (end_of(clip) or 0) > start]
        newest = max(starts, default=None)
        if newest is not None:
            attempted, fetch = self._frame_attempts.get(index, (None, None))
            if attempted != newest:
                fetch = self.hass.async_create_task(
                    self._fetch_frame(camera, newest))
                self._frame_attempts[index] = (newest, fetch)
            # Shielded, so a viewer closing the tab mid-fetch cancels its own
            # wait and not the fetch: the attempt is already marked, so a
            # cancelled fetch would leave this clip marked as tried and never
            # tried, and the old frame would stay until the next event.
            await asyncio.shield(fetch)
        return await async_latest_image(self.hass, camera)

    async def _fetch_frame(self, camera: dict, start_time: int) -> None:
        """Fetch one clip's frame, then keep the strays in check.

        Here rather than on a timer because this is the only thing that makes
        them: one arrives, one is swept, and a camera nobody looks at costs
        nothing.
        """
        await async_preview_clip(self.hass, self.client, camera, start_time)
        for removed in await async_prune_previews(self.hass, camera):
            _LOGGER.debug("Pruned stray preview %s", removed)

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

    def latch_silent(self, index: int, tripped: bool) -> bool:
        """Hold a silence alarm until the camera actually records again.

        The adaptive half of the test un-fires as the silence lengthens, and
        that is the opposite of what a watchdog owes anyone. Its baseline is
        drawn from the clips still inside the poll window, so the evidence
        ages out while the camera stays dark: a doorbell doing thirty a day
        trips at nine hours, reads healthy again at twelve as its own history
        scrolls out of the window, and predicts nothing at all past a day.
        Measured against a real outage, not reasoned about.

        The ceiling still catches it at the configured hours, so the fault was
        never invisible -- but an alarm that switches itself off while the
        fault is still there is worse than one that never fired, because the
        dashboard says fine and the automation sees no transition to act on.

        So the trip latches, and only the camera recording again clears it --
        the same rule the restart breaker uses, for the same reason.
        """
        last = self.last_activity(index)
        if index in self._silent_latched:
            # A stamp that has moved is a recording that arrived after the
            # alarm tripped. Nothing else counts as recovery: the expectation
            # falling back under its line is the decay this exists to ignore.
            if last is not None and last != self._silent_latched[index]:
                del self._silent_latched[index]
            else:
                return True
        if tripped:
            self._silent_latched[index] = last
        return tripped

    def silent_latched(self, index: int) -> bool:
        """Whether this camera's alarm is being held rather than re-proved."""
        return index in self._silent_latched

    def tampered(self, within: int) -> list[tuple[str, int]]:
        """Every report of a camera being interfered with, newest first.

        The binary sensor for code 19 holds for thirty seconds and then clears,
        which is right for a history graph and wrong for this: somebody lifting
        a camera off its mount is a fact the owner needs whenever they next
        open Home Assistant, not only if they happened to be looking.
        """
        since = int(dt_util.utcnow().timestamp()) - within
        found: list[tuple[str, int]] = []
        for index, camera in enumerate(self.cameras):
            for clip in self.clips_for(index):
                moment = start_of(clip)
                if moment is None or moment < since:
                    continue
                if has_detection(clip, TAMPER_CODES):
                    found.append(
                        (camera.get("alias") or f"Camera {index}", moment))
        found.sort(key=lambda item: item[1], reverse=True)
        return found

    def silent_cameras(self, threshold: int) -> list[str]:
        """The names of every camera quiet for longer than `threshold`."""
        quiet = []
        for index, camera in enumerate(self.cameras):
            seconds = self.silent_seconds(index)
            if seconds is not None and seconds >= threshold:
                quiet.append(camera.get("alias") or f"Camera {index}")
        return quiet

    async def async_refresh_after_write(self) -> None:
        """Refresh once after a write, and make that poll read status.

        The refresh is the one the caller was already making -- the flag only
        changes what it fetches, never how often. Asking for a second poll
        would be exactly the traffic this hub wedges under.
        """
        self._force_status = True
        await self.async_request_refresh()

    def _pace(self) -> None:
        """Set the poll interval to suit what the hub and the house are doing.

        Three states, in that order of precedence. A hub that is not answering
        backs off hard, because pytapo re-authenticates when its token stops
        working and a stream of fresh logins is what wedges an H500. A house
        where nothing has happened for ten minutes backs off gently, because
        most of this integration's traffic is asking a quiet hub whether
        anything happened yet. Anything else runs at full speed.

        Deliberately one place. Two independent writers of `update_interval`
        would each undo the other depending on which poll finished last.
        """
        if self._failures:
            wanted = backoff_seconds(
                self._base_interval, self._failures, POLL_BACKOFF_MAX)
            why = "hub not answering"
        elif (dt_util.utcnow().timestamp() - self._last_activity_at
                >= POLL_IDLE_AFTER):
            # Never faster than configured: this only ever slows things down.
            wanted = max(self._base_interval, POLL_IDLE_INTERVAL)
            why = "nothing happening"
        else:
            wanted = self._base_interval
            why = "recent activity"
        if wanted != (self.update_interval.total_seconds()
                      if self.update_interval else None):
            _LOGGER.debug("Polling every %ss (%s)", wanted, why)
            self.update_interval = timedelta(seconds=wanted)

    async def _async_update_data(self) -> dict:
        """Poll, paced to what the hub and the house are doing."""
        try:
            data = await self._poll()
        except Exception:
            self._failures += 1
            self._pace()
            raise
        self._failures = 0
        self._pace()
        return data

    async def _poll(self) -> dict:
        # Counted up front, not on the way out.
        #
        # Every cadence below is `poll % every == 0`, and the counter used to
        # advance only where a poll finished. So it froze the moment the hub
        # stopped answering -- and whichever gates happened to be open at that
        # instant stayed open for the whole outage. A hub that was already
        # failing got its camera list re-fetched, its status read and its
        # media port handshaken on every single retry, which is the opposite
        # of what a struggling device needs. Reading the old value keeps poll
        # zero doing everything, which is what leaves nothing blank on startup.
        poll, self._polls = self._polls, self._polls + 1

        # The paired camera list changes only when a camera is added or
        # removed, and costs 58ms -- more than the detection lookups it used to
        # precede. Fetch it on the first poll and then rarely, but never leave
        # it empty: a failure with nothing cached is still fatal to the poll.
        if not self.cameras or poll % self._cameras_every == 0:
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
                fetch = getattr(self.client, "activity", None)
                if fetch is not None:
                    # One round trip for both searches -- proven on hardware,
                    # and half the per-poll load on a hub that is easy to
                    # overload.
                    clips, detections = await self.hass.async_add_executor_job(
                        fetch, camera, window, now + 60)
                else:
                    # Clients without the batched call: the test doubles, and
                    # the exact behaviour this had before batching existed.
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
        if self._force_status or poll % self._status_every == 0:
            self._force_status = False
            try:
                # Kept alongside the parsed readings, for diagnostics. The
                # parser reads the keys it knows and drops the rest, so a
                # field a newer firmware added is invisible to everything --
                # which is how `detect_status` went unnoticed until somebody
                # dumped the JSON by hand.
                self.raw_status = await self.hass.async_add_executor_job(
                    self.client.hub_status)
                self.readings = hub_readings(self.raw_status)
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
        # The media port's handshake, on its own slow cadence. Skipped while
        # a session is open: an extra connection against a hub mid-download
        # is a variable the wedge investigation does not need.
        check = getattr(self.client, "check_media", None)
        lock = getattr(self.client, "_lock", None)
        if (check is not None and poll % self._media_every == 0
                and not (lock is not None and lock.locked())):
            try:
                self.note_media_status(
                    await self.hass.async_add_executor_job(check))
            except Exception as err:  # noqa: BLE001 - a health check must not hurt
                _LOGGER.debug("Media port check failed: %s", err)
            # The case-D experiment: one fresh player_id per wedge episode,
            # so the next session's log line answers whether stale hub state
            # is keyed to the reused id. Once, not per poll -- repeating it
            # would erase the evidence of whether one rotation was enough.
            rotate = getattr(self.client, "rotate_player_id", None)
            if self.media_status == "wedged" and not self._wedge_rotated:
                if rotate is not None:
                    rotate()
                    self.note_recovery_attempt("player id rotated")
                self._wedge_rotated = True
            elif self.media_status == "healthy":
                self._wedge_rotated = False

        # The deep check: when no media session has taught us anything for
        # an hour and an indexed clip exists, fetch two seconds of it in the
        # background and feed the same counters the downloads feed. This is
        # what finds the serving-empty failure on a quiet day -- and what
        # notices recovery without waiting for a download. Skipped while a
        # session is in flight (evidence is already on its way) and for
        # clients without the call (the test doubles).
        # The post-restart recheck: minutes after an automatic restart,
        # force the deep check due. Bytes prove the cure and re-arm
        # everything; an empty answer feeds the breaker while the failure
        # is still inside its cure window.
        if self._recheck_at is not None and now >= self._recheck_at:
            self._recheck_at = None
            self._media_evidence = 0.0

        fetchable = getattr(self.client, "iter_recording", None)
        spawn = getattr(self.entry, "async_create_background_task", None)
        lock = getattr(self.client, "_lock", None)
        if (fetchable is not None and spawn is not None
                and now - self._media_evidence >= MEDIA_EVIDENCE_MAX_AGE
                and not (lock is not None and lock.locked())):
            newest = None
            for index in clips_by_camera:
                for clip in clips_by_camera[index]:
                    start, end = start_of(clip), end_of(clip)
                    if (start and end and end > start
                            and (newest is None or start > newest[1])):
                        newest = (index, start, end)
            if newest is not None:
                index, start, end = newest
                # Provisional stamp so the next 2-second poll does not
                # spawn a second fetch while this one runs.
                self._media_evidence = now
                spawn(self.hass,
                      self._deep_media_check(cameras[index], start, end),
                      f"{DOMAIN} media health check")

        # Opt-in self-healing. Both media failure modes -- refused sessions
        # and the hollow ones -- are cured by a reboot and by nothing else
        # ever found, so with the owner's say-so the coordinator presses its
        # own restart button: once, loudly, and never inside the cooldown,
        # which makes a reboot loop impossible however long a failure
        # persists. Everything else (dark cameras included) stays hands-off
        # -- rebooting the hub at flat camera batteries gains nothing.
        if self.entry.options.get(CONF_AUTO_RESTART, DEFAULT_AUTO_RESTART):
            reason = ("wedged" if self.media_status == "wedged"
                      else "empty" if self.media_serving_empty else None)
            reboot = getattr(self.client, "reboot", None)
            elapsed = now - self._auto_restarted
            # The circuit breaker: a failure back within half an hour of a
            # restart means restarting is not the cure -- a new failure in
            # a familiar coat, which a reboot every six hours would mask
            # forever. Only real recovery (bytes served) re-arms it.
            if (reason is not None and not self._auto_restart_broken
                    and 0 < elapsed <= AUTO_RESTART_CURE_WINDOW):
                self._auto_restart_broken = True
                _LOGGER.warning(
                    "The automatic restart did not cure the media failure "
                    "(%s is back within %.0f minutes); automatic restarts "
                    "are paused until recordings actually serve again",
                    reason, elapsed / 60)
            if (reason is not None and reboot is not None
                    and not self._auto_restart_broken
                    and elapsed >= AUTO_RESTART_COOLDOWN):
                self._auto_restarted = now
                self.note_restarting()
                _LOGGER.warning(
                    "Restarting the hub automatically: media service %s. "
                    "Expect about two minutes of downtime. Turn this off "
                    "under Configure if it was not wanted.", reason)
                self._recheck_at = now + AUTO_RESTART_RECHECK
                self.hass.bus.async_fire(EVENT_AUTO_RESTART, {
                    "entry_id": self.entry.entry_id, "reason": reason})
                try:
                    await self.hass.async_add_executor_job(reboot)
                except Exception as err:  # noqa: BLE001 - the drop IS the reboot
                    _LOGGER.debug("Restart call ended with %s, which is "
                                  "what a reboot looks like",
                                  type(err).__name__)

        # What the hub already knows about newer firmware, a few times a
        # day. A local read of its cached block -- nothing commands it to
        # contact TP-Link; see H500Client.firmware_update.
        firmware = getattr(self.client, "firmware_update", None)
        if firmware is not None and poll % self._firmware_every == 0:
            try:
                self.firmware_info = await self.hass.async_add_executor_job(
                    firmware)
            except Exception as err:  # noqa: BLE001 - a check must not hurt
                _LOGGER.debug("Firmware check failed: %s", err)

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
        self._primed = True
        # Raise or clear the repair issues. Called every poll rather than only
        # on failure, because an issue that never clears is worse than none.
        try:
            from .repairs import async_check
            async_check(self.hass, self.entry.entry_id, self)
        except Exception as err:  # noqa: BLE001 - never fail a poll over this
            # Loudly the first time, quietly after. Every repair notice this
            # integration raises comes through here, so one exception silences
            # all nine -- the storage warning, the wedge, the silent camera --
            # and at debug level nobody ever learns why the notices stopped.
            # Warning on every poll would be a line every two seconds, which
            # is its own way of being unreadable, so it is said once and then
            # only again after the checks have worked in between.
            if not self._repairs_broken:
                self._repairs_broken = True
                _LOGGER.warning(
                    "Repair notices are not being updated: %s. Every notice "
                    "this integration raises comes from here, so none of them "
                    "is reliable until this is fixed", err)
            else:
                _LOGGER.debug("Repair notices still failing: %s", err)
        else:
            if self._repairs_broken:
                self._repairs_broken = False
                _LOGGER.warning("Repair notices are being updated again")
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
        if fresh:
            # Set here rather than in the two callers because this is the one
            # place "new" is decided, for detections and for clips alike. It
            # is what keeps the idle backoff off while anything is going on.
            self._last_activity_at = dt_util.utcnow().timestamp()
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

    def note_empty_download(self) -> None:
        self._empty_downloads += 1
        self._media_evidence = dt_util.utcnow().timestamp()
        self._note_wedge_state()

    def note_served_download(self) -> None:
        recovering = self.media_serving_empty
        self._empty_downloads = 0
        self._media_evidence = dt_util.utcnow().timestamp()
        # Real bytes flowed: whatever was wrong is over, so a tripped
        # breaker re-arms and future failures may be auto-cured again.
        self._auto_restart_broken = False
        if recovering:
            # Every clip during the outage burned its one frame-fetch
            # attempt on an empty answer. Clearing the marks lets the
            # camera picture repair itself at the next look instead of
            # staying stale until the next event. Only on recovery: a
            # routine download clearing them would invite a redundant
            # refetch per clip.
            self._frame_attempts.clear()
            self._retry_failed_clips()
        self._note_wedge_state()

    def _remember_failed_clip(self, index: int, start_time: int) -> None:
        """Remember a clip that did not download, for one more try later.

        Not retried on the next poll. The failure that causes this in bulk is
        the media service wedging, and then every clip in the window has
        failed -- retrying them two seconds later means the whole window
        hammering a device that is already refusing, which is how this hub
        gets worse rather than better.

        So the retry rides the recovery signal instead: the moment bytes
        provably flow again, everything that failed during the outage is put
        back in the queue. Until then the clips stay on the hub, which holds
        about a fortnight of them, so waiting costs nothing and asking costs
        the one thing that is scarce.
        """
        attempts = self._failed_clips.setdefault(index, {})
        attempts[start_time] = attempts.get(start_time, 0) + 1

    def _retry_failed_clips(self) -> None:
        """Un-mark everything that failed during the outage just ended.

        A clip is skipped because its start time is in `_seen_clips`, so
        forgetting it there is what puts it back in the download queue -- the
        same move the does-not-decode path already makes for the same reason.

        Capped: a clip that has failed DOWNLOAD_RETRY_LIMIT times is failing
        for its own reason rather than the hub's, and retrying it forever
        would spend a media session per poll on a recording that will never
        arrive. The repairs notice already names a pipeline failing this way.
        """
        for index, attempts in self._failed_clips.items():
            seen = self._seen_clips.get(index)
            if not seen:
                continue
            for start_time, count in attempts.items():
                if count < DOWNLOAD_RETRY_LIMIT:
                    seen.discard((start_time,))

    def note_media_status(self, status: str) -> None:
        """Record the sentinel's verdict, noticing recovery on the way."""
        if status == "healthy" and self.media_status == "wedged":
            self._frame_attempts.clear()
        self.media_status = status
        self._note_wedge_state()

    @property
    def media_wedged(self) -> bool:
        """Whether the hub is refusing to serve recordings right now.

        Either signal is enough and they are independent: the sentinel's
        handshake against port 8800, and two clean-but-empty downloads in a
        row. Serving-empty counts whether or not a handshake has ever run,
        because the downloads themselves are the evidence.
        """
        return self.media_serving_empty or self.media_status == "wedged"

    def _note_wedge_state(self) -> None:
        """Keep the log of when this hub stopped serving and started again.

        The hub keeps no such history, and neither does Home Assistant in any
        form that outlives a purge: binary sensors get no long-term
        statistics, so the wedge sensor's own history ends there. This is what
        makes "how often, and how long between" answerable months later, which
        is the question a support case turns on.
        """
        wedged = self.media_wedged
        if wedged == self._was_wedged:
            return
        self._was_wedged = wedged
        now = dt_util.utcnow().timestamp()
        if wedged:
            self._longest_healthy = max(
                self._longest_healthy, now - self._healthy_since)
            self.wedges.append({"at": now, "tried": [], "ended": None})
            # A wedge every twelve hours over the kept window is a few hundred
            # small records. Older than that has stopped being evidence about
            # the hub as it is now.
            cutoff = now - WEDGE_HISTORY_SECONDS
            self.wedges = [w for w in self.wedges if w["at"] >= cutoff]
        else:
            self._healthy_since = now
            if self.wedges and self.wedges[-1]["ended"] is None:
                self.wedges[-1]["ended"] = now

    def note_recovery_attempt(self, what: str) -> None:
        """Record something tried to cure the outage that is running.

        Every wedge used to start the diagnosis from nothing: the log showed
        that it happened and never what was done about it, so "does
        restarting actually help?" stayed a matter of memory. Attached to the
        open episode, beside when it ended, which is what makes the two
        readable together.

        Ignored when nothing is wrong, because there is no episode for it to
        belong to and inventing one would report an outage that never was.
        """
        if self.wedges and self.wedges[-1]["ended"] is None:
            self.wedges[-1]["tried"].append(
                {"what": what, "at": dt_util.utcnow().timestamp()})

    def note_restarting(self) -> None:
        """The hub is being rebooted to cure a media failure.

        The empty-download counter starts fresh, because sessions from before
        a reboot say nothing about the hub after it. Deliberately not
        `note_served_download`, which this borrowed: that one means bytes
        arrived. It clears the frame marks, retries the failed clips and
        closes the episode in the wedge log -- all of it claiming a recovery
        that at this point is only a hope. Whether the restart worked is what
        the next session gets to decide.
        """
        self._empty_downloads = 0
        self._media_evidence = dt_util.utcnow().timestamp()
        self.note_recovery_attempt("hub restart")

    def recovery_log(self, limit: int = 10) -> list[dict]:
        """The newest episodes first, in the shape a person reads.

        Times as ISO strings and lengths in minutes, because the audience is
        somebody reading a diagnostics file or a support thread, not code.
        """
        entries = []
        for wedge in reversed(self.wedges[-limit:]):
            ended = wedge["ended"]
            entries.append({
                "at": dt_util.utc_from_timestamp(wedge["at"]).isoformat(),
                "lasted_minutes": (
                    None if ended is None
                    else round((ended - wedge["at"]) / 60, 1)),
                "tried": [
                    {"what": attempt["what"],
                     "after_minutes":
                         round((attempt["at"] - wedge["at"]) / 60, 1)}
                    for attempt in wedge["tried"]],
            })
        return entries

    @property
    def healthy_seconds(self) -> float:
        """How long the media path has been serving, this run.

        Zero while it is not, climbing while it is -- so the recorder's
        long-term graph is a sawtooth whose peaks are the times to wedge and
        whose resets are the wedges. One number answering how often, how long
        between, and what the best run was.
        """
        if self.media_wedged:
            return 0.0
        return max(0.0, dt_util.utcnow().timestamp() - self._healthy_since)

    def wedges_since(self, seconds: float) -> int:
        cutoff = dt_util.utcnow().timestamp() - seconds
        return sum(1 for wedge in self.wedges if wedge["at"] >= cutoff)

    @property
    def longest_healthy_seconds(self) -> float:
        """The best run yet, the one in progress included."""
        return max(self._longest_healthy, self.healthy_seconds)

    async def _deep_media_check(self, camera, start: int, end: int) -> None:
        """Two bounded seconds of the newest clip, as evidence.

        Bytes are the all-clear -- recovery gets noticed without waiting
        for a download. An empty answer could be one freak clip, so it is
        confirmed with a second fetch moments later rather than a second
        quiet hour later; two empties flag the state exactly as two empty
        downloads do. An error is inconclusive: the wedge has its own
        detector, and a hub mid-reboot must not be miscounted.
        """
        for attempt in range(2):
            received = 0
            try:
                async for chunk in self.client.iter_recording(
                        camera, start, min(end, start + 2),
                        kind="healthcheck"):
                    received += len(chunk)
            except Exception as err:  # noqa: BLE001 - inconclusive by design
                _LOGGER.debug("Media health fetch inconclusive: %s", err)
                return
            if received:
                self.note_served_download()
                return
            self.note_empty_download()
            if self.media_serving_empty:
                return
            await asyncio.sleep(EMPTY_CONFIRM_DELAY)

    @property
    def auto_restart_broken(self) -> bool:
        """An automatic restart failed to cure the failure it fired for."""
        return self._auto_restart_broken

    @property
    def media_serving_empty(self) -> bool:
        """Two clean-but-empty downloads in a row: the hub serves nothing.

        One could be a freak clip; two consecutive recordings with real
        durations answering zero bytes is the state measured on hardware,
        where it held for every clip of every age. A single served download
        clears it.
        """
        return self._empty_downloads >= 2

    @property
    def download_failures(self) -> dict[str, int]:
        """Camera name -> consecutive failed automatic downloads."""
        found = {}
        for index, count in self._download_failures.items():
            if count <= 0:
                continue
            name = (self.cameras[index].get("alias")
                    if index < len(self.cameras) else None)
            found[name or f"Camera {index}"] = count
        return found

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
                detected=detection_types(clip), faces=face_ids(clip),
            )
        except EmptyRecordingError as err:
            _LOGGER.warning("Automatic download of clip %s failed: %s",
                            start_time, err)
            self._download_failures[index] = (
                self._download_failures.get(index, 0) + 1)
            self._remember_failed_clip(index, start_time)
            self.note_empty_download()
            return
        except HomeAssistantError as err:
            _LOGGER.warning("Automatic download of clip %s failed: %s",
                            start_time, err)
            self._download_failures[index] = (
                self._download_failures.get(index, 0) + 1)
            self._remember_failed_clip(index, start_time)
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
            # Bytes arrived and did not decode: still a pipeline failing.
            self._download_failures[index] = (
                self._download_failures.get(index, 0) + 1)
            return
        self._download_failures.pop(index, None)
        self._failed_clips.get(index, {}).pop(start_time, None)
        self.note_served_download()
        # Only automatic downloads are pruned. A manual download is a
        # deliberate choice and is left alone.
        keep = self.entry.options.get(CONF_KEEP_DOWNLOADS, DEFAULT_KEEP_DOWNLOADS)
        for removed in await async_prune(self.hass, camera, keep,
                                         self._protected(index)):
            _LOGGER.debug("Pruned %s to keep the newest %s", removed, keep)
        async_dispatcher_send(self.hass, self.signal("image", index))
