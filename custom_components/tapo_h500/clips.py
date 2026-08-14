"""Interpreting what the hub says about clips and detections.

Pure functions over the hub's response dictionaries. No network, no
filesystem, no Home Assistant — so this is the part that can be tested
without a hub or an installed Home Assistant.
"""
from __future__ import annotations

import re

from .const import (
    DETECTION_NAMES, EVENT_MOTION, EVENT_RING, RING_ALARM_TYPES, RING_HINTS,
)

# Fields the hub has been seen to label activity with, most specific first.
TYPE_FIELDS = ("video_type", "detection_type", "event_type", "type")


def camera_slug(camera: dict) -> str:
    """A filesystem-safe token for a camera's alias."""
    alias = camera.get("alias") or camera.get("device_name") or "camera"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(alias)).strip("_.").lower()
    return (slug or "camera")[:60]


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def start_of(entry: dict) -> int | None:
    """Clips use startTime, detections use start_time."""
    return _as_int(entry.get("startTime", entry.get("start_time")))


def end_of(entry: dict) -> int | None:
    return _as_int(entry.get("endTime", entry.get("end_time")))


def hub_label(entry: dict) -> str | None:
    """The hub's own activity label, whichever field it arrived in."""
    for field in TYPE_FIELDS:
        if entry.get(field):
            return str(entry[field])
    return None


def detection_types(entry: dict) -> list[int]:
    """Every alarm type in one detection, lowest first.

    searchDetectionList carries two related fields, and across every detection
    observed on firmware 1.3.20 `alarm_type` was exactly the highest set bit of
    `events_1` plus one:

        alarm_type  2 -> events_1 bit 1     alarm_type 17 -> bit 16
        alarm_type  6 -> bit 5              alarm_type 19 -> bit 18
        alarm_type  8 -> bit 7              alarm_type 20 -> bit 19
        alarm_type  9 -> bit 8              alarm_type 22 -> bit 21

    So the mask is the richer field: it lists everything that fired at once,
    where alarm_type reports only the most significant. The mask is preferred
    and alarm_type is the fallback for a detection that carries no mask.
    """
    mask = _as_int(entry.get("events_1"))
    if mask is not None and mask > 0:
        return [bit + 1 for bit in range(mask.bit_length()) if mask >> bit & 1]
    alarm = _as_int(entry.get("alarm_type"))
    return [alarm] if alarm else []


def face_ids(entry: dict) -> list[int]:
    """The hub's identifier for each face it recognised in this recording.

    The hub gives a number and nothing else — no name and no image. There is no
    face library to look the number up in: getFaceList, getFaceInfo,
    searchFaceList and getFaceLibrary are all -40106, and
    getFaceDetectionConfig returns the same config whatever section is asked
    for. The accompanying `face_bitmap` has been 0 on every detection observed,
    so it categorises nothing either.

    The number is still worth having: the same person appears to keep the same
    id, so an automation can match one and supply the name the hub will not.
    """
    found = []
    for event in entry.get("event_info") or []:
        if not isinstance(event, dict):
            continue
        identifier = _as_int(event.get("face_id"))
        if identifier is not None and identifier not in found:
            found.append(identifier)
    return found


def primary_type(entry: dict) -> int | None:
    """The most significant alarm type, which is what alarm_type reports."""
    alarm = _as_int(entry.get("alarm_type"))
    if alarm:
        return alarm
    types = detection_types(entry)
    return types[-1] if types else None


def describe_detection(entry: dict) -> str | None:
    """A short phrase for what the hub says triggered this recording.

    Unnamed codes are shown as their number rather than guessed at, so an
    unfamiliar code reads as "type 22" instead of silently becoming "motion".
    """
    types = detection_types(entry)
    if not types:
        return None
    # 10 is a property of a press, not an event beside it, and it has never
    # appeared without 17. Listing both gives "missed doorbell + doorbell",
    # which announces the same press twice and reads as a contradiction, so
    # the pair collapses into one phrase.
    if 10 in types and 17 in types:
        types = [code for code in types if code != 10]
        press = "doorbell (missed)"
    else:
        press = None
    named = [press if code == 17 and press else DETECTION_NAMES[code]
             for code in types if code in DETECTION_NAMES]
    unknown = [f"type {code}" for code in types if code not in DETECTION_NAMES]
    return " + ".join(named + unknown) or None


def event_type(entry: dict) -> str:
    """Classify one clip or detection as a doorbell press or motion.

    The hub's per-clip `video_type` is "2" for everything, so it classifies
    nothing; the detection log is what carries the real type. Which alarm_type
    means a doorbell press has not been captured yet, so RING_ALARM_TYPES is
    empty and nothing claims to be a ring — add the code there once a real
    press has been seen and every path picks it up.
    """
    for code in detection_types(entry):
        if code in RING_ALARM_TYPES:
            return EVENT_RING
    label = (hub_label(entry) or "").lower()
    if label and any(hint in label for hint in RING_HINTS):
        return EVENT_RING
    return EVENT_MOTION


# Detections and clips are separate lookups. Every clip observed had a
# detection starting at the same second, but a second of slack costs nothing
# and covers a clock that rounds.
MATCH_SECONDS = 2


def attach_detections(clips: list[dict], detections) -> list[dict]:
    """Copy each clip's detection fields onto it, so one record carries both."""
    by_time = {}
    for detection in detections or []:
        moment = start_of(detection)
        if moment is not None:
            by_time[moment] = detection
    if not by_time:
        return clips
    for clip in clips:
        moment = start_of(clip)
        if moment is None:
            continue
        match = by_time.get(moment) or next(
            (found for when, found in by_time.items()
             if abs(when - moment) <= MATCH_SECONDS), None)
        if match is None:
            continue
        for field in ("alarm_type", "events_1", "event_info"):
            if match.get(field) is not None:
                clip[field] = match[field]
    return clips


def flatten_clips(result: dict) -> list[dict]:
    """Pull clip dictionaries out of a searchVideoWithUTC response."""
    found = []
    for group in result.get("playback", {}).get("search_video_results", []):
        if not isinstance(group, dict):
            continue
        for clip in group.values():
            if isinstance(clip, dict) and "startTime" in clip:
                found.append(clip)
    return found


def surplus(items, keep: int) -> list:
    """Everything past the newest `keep`, oldest first.

    `items` must already be in oldest-to-newest order. Returns nothing when
    `keep` is zero or negative, which is how "no limit" is expressed — the
    caller deletes what comes back, so an off-by-one here loses recordings.
    """
    if keep <= 0 or len(items) <= keep:
        return []
    return list(items[:-keep])


def events_since(clips: list[dict], since: int) -> int:
    """How many of these recordings started at or after a moment."""
    return sum(1 for clip in clips
               if (start_of(clip) or 0) >= since)


def hourly_baseline(clips: list[dict], now: int, window: int) -> float:
    """The camera's own typical events-per-hour over the polled window.

    Deliberately the camera's own history rather than a fixed threshold: a
    doorbell on a main road might see forty events a day and a back gate two,
    and "busy" means something different at each. Anything older than the
    window is not available -- the integration holds 24 hours, not a database
    -- so this is a same-day baseline, which is the honest limit of it.
    """
    hours = max(1.0, window / 3600)
    counted = events_since(clips, now - window)
    return counted / hours


def unusually_busy(clips: list[dict], now: int, window: int,
                   multiplier: float, floor: int) -> bool:
    """Whether the last hour stands out against this camera's own baseline.

    Two guards, because a ratio alone is useless at both ends. A camera that
    normally sees nothing has a baseline of zero, and any event at all would
    be infinitely unusual -- hence the floor, below which nothing is flagged.
    And a camera that is always busy should not flag continuously, which is
    what the multiplier is for.
    """
    recent = events_since(clips, now - 3600)
    # One expression, deliberately: an earlier version also returned early
    # below the floor, which enforced it twice. Removing either guard then
    # changed no behaviour, so no test could tell a broken one from a working
    # one -- the floor lives here and only here.
    return recent >= max(floor, hourly_baseline(clips, now, window) * multiplier)


def sessions(clips: list[dict], gap: int) -> list[tuple[int, int, int]]:
    """Recordings grouped into visits, as (start, end, recordings).

    The hub reports moments, not presence: someone standing at the door for
    four minutes produces a string of short clips, not one long one, and every
    count in this integration treats those as separate events. Two recordings
    closer together than `gap` are one visit.

    Oldest first. Recordings with no start time are dropped -- there is
    nowhere to put them in a timeline.
    """
    spans = sorted((start, end_of(clip) or start)
                   for clip in clips
                   for start in [start_of(clip)] if start is not None)
    visits: list[tuple[int, int, int]] = []
    for start, end in spans:
        if visits and start - visits[-1][1] <= gap:
            began, finished, count = visits[-1]
            visits[-1] = (began, max(finished, end), count + 1)
        else:
            visits.append((start, end, 1))
    return visits


def loitering(clips: list[dict], now: int, gap: int, minimum: int) -> int:
    """How long an unrecognised face has been around, if it still is.

    Zero unless all three hold: the face is one the hub could not match,
    the visit is still open, and it has lasted long enough to be worth the
    word. This is the difference between someone reading a house number and
    someone standing at the door for four minutes, and nothing else here can
    tell them apart -- the busy-camera signal is a rate over an hour, and the
    night signal is about the clock.

    Measured from the first sighting to the last, not to `now`. A single
    fifteen-second clip is evidence of fifteen seconds; counting the silence
    since would inflate every brief visit the moment it ended.
    """
    unknown = [clip for clip in clips if 22 in detection_types(clip)]
    visits = sessions(unknown, gap)
    if not visits:
        return 0
    start, end, _ = visits[-1]
    # Over. Someone who left two hours ago is not loitering now.
    if now - end > gap:
        return 0
    lasted = end - start
    return lasted if lasted >= minimum else 0


def summarise(per_camera: dict[str, list[dict]], now: int,
              window: int = 86400) -> str:
    """A day in one sentence per camera, for a digest or a spoken answer.

    Returns prose rather than a structure because both callers want prose, and
    a shared phrasing is what stops the digest and the voice answer describing
    the same day differently.
    """
    lines = []
    for name, clips in per_camera.items():
        recent = [clip for clip in clips if (start_of(clip) or 0) >= now - window]
        if not recent:
            lines.append(f"{name}: nothing")
            continue
        counts: dict[str, int] = {}
        for clip in recent:
            for code in detection_types(clip):
                # Motion accompanies nearly everything and would dominate a
                # summary that is meant to say what was unusual about the day.
                if code in (2, 10):
                    continue
                label = DETECTION_NAMES.get(code)
                if label:
                    counts[label] = counts.get(label, 0) + 1
        if not counts:
            lines.append(f"{name}: {len(recent)} recordings, motion only")
            continue
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        detail = ", ".join(f"{count} {label}" for label, count in ranked)
        lines.append(f"{name}: {len(recent)} recordings ({detail})")
    return "; ".join(lines) if lines else "nothing"


def direction(trail: list[dict], ranks: dict[str, int],
              window: int) -> str | None:
    """Whether a trail reads as approaching, leaving, or neither.

    `trail` is newest first, as faces_seen builds it. `ranks` maps a camera
    name to its distance from the street: lower is nearer the street, higher
    nearer the door.

    None whenever the answer is not actually known -- one sighting, cameras
    with no rank, two sightings too far apart to be one journey, or a move
    between cameras at the same rank. A guessed direction is worse than none,
    because "someone is approaching the door" is the kind of thing people
    build a siren automation on.
    """
    ranked = [hop for hop in trail if hop.get("camera") in ranks]
    if len(ranked) < 2:
        return None
    newest, previous = ranked[0], ranked[1]
    # Both times are required rather than defaulted. Treating a missing one as
    # zero makes two undated hops look simultaneous, which passes the window
    # check and invents a direction -- and "approaching" is what people wire a
    # siren to.
    when, before_when = newest.get("at"), previous.get("at")
    if when is None or before_when is None:
        return None
    if when - before_when > window:
        return None
    here, before = ranks[newest["camera"]], ranks[previous["camera"]]
    if here == before:
        return None
    return "approaching" if here > before else "leaving"


def in_night(hour: int, start: int, end: int) -> bool:
    """Whether an hour falls in the night window, which wraps midnight.

    22 to 6 is not a range in the ordinary sense: 23 is inside it and 12 is
    not, but 23 > 6. Comparing naively marks the whole day as night, which is
    the obvious way to get this wrong.
    """
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def notable(entry: dict, hour: int, start: int, end: int) -> bool:
    """An unfamiliar face, at night.

    Deliberately narrow. Motion at night is a cat, and a recognised face at
    night is someone coming home; neither deserves a different alarm sound.
    An unrecognised face is the one combination worth waking someone for, and
    widening it is how a signal becomes noise and gets muted.
    """
    return 22 in detection_types(entry) and in_night(hour, start, end)


def busiest_hour(clips: list[dict]) -> int | None:
    """The local hour with the most recordings, or None if there are none.

    Ties go to the earlier hour. Deliberately: a quiet camera has many hours
    holding one event each, and picking the latest would make the answer jump
    around as the day filled up.
    """
    hours = [0] * 24
    for clip in clips:
        moment = start_of(clip)
        if moment is None:
            continue
        hours[_local_hour(moment)] += 1
    peak = max(hours)
    return hours.index(peak) if peak else None


def _local(moment: int):
    from homeassistant.util import dt as dt_util
    return dt_util.as_local(dt_util.utc_from_timestamp(moment))


def _local_hour(moment: int) -> int:
    return _local(moment).hour


def local_date(moment: int) -> str:
    """Which local calendar day a moment falls on, as YYYY-MM-DD.

    Local rather than UTC because "today" is a human word. Someone arriving
    home at 00:30 has arrived on the new day, and in any timezone west of
    Greenwich a UTC day boundary would put that back on the previous one.
    """
    return _local(moment).date().isoformat()


def unique_faces(clips: list[dict]) -> int:
    """How many distinct people the hub recognised, named or not."""
    return len({face for clip in clips for face in face_ids(clip)})


def unknown_face_count(clips: list[dict]) -> int:
    """Recordings carrying a face the hub could not match to anyone."""
    return sum(1 for clip in clips if 22 in detection_types(clip))
