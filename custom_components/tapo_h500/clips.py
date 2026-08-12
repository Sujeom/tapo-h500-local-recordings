"""Interpreting what the hub says about clips and detections.

Pure functions over the hub's response dictionaries. No network, no
filesystem, no Home Assistant — so this is the part that can be tested
without a hub or an installed Home Assistant.
"""
from __future__ import annotations

import re

from .const import EVENT_MOTION, EVENT_RING, RING_HINTS

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


def event_type(entry: dict) -> str:
    """Classify one clip or detection as a doorbell press or motion.

    ponytail: an H500 with TD21 doorbells reports numeric labels — every clip
    observed came back video_type "2" — and which code means a press is not
    known, so nothing is classified as a ring yet. The raw label rides along on
    the event as hub_type; once a real press is seen with a different code, add
    it here. Until then treat "ring" as unreachable rather than trusted.
    """
    label = (hub_label(entry) or "").lower()
    if label and any(hint in label for hint in RING_HINTS):
        return EVENT_RING
    return EVENT_MOTION


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
