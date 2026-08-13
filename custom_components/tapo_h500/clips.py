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
