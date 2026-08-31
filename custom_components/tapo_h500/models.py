"""The shapes the hub sends, named.

These travel through a dozen modules, and they are where a wrong assumption
hides: this integration has already had a record nested two levels deeper
than the code expected, where every read silently returned the default, the
model came back empty and the guard that should have refused a C200 never
ran. Nothing failed; the file simply said nothing.

`total=False` throughout, deliberately. The hub omits fields depending on
firmware, on whether a camera is battery powered, and on whether a detection
carried a face -- so every reader has to survive absence, and a TypedDict
that promised presence would be a lie that type checking made harder to see.
"""
from __future__ import annotations

from typing import Any, TypedDict


class Camera(TypedDict, total=False):
    """One entry of the hub's paired list."""

    device_id: str
    alias: str
    device_name: str
    device_model: str
    mac: str
    battery_percent: int
    hub_storage_enabled: bool


class Clip(TypedDict, total=False):
    """One indexed recording, as searchDetectionList returns it.

    `events_1` is a bitmask where detection code n is bit n-1, which is why
    nothing reads it directly outside clips.py.
    """

    startTime: int
    endTime: int
    events_1: int
    event_info: list[dict[str, Any]]


class Detection(TypedDict, total=False):
    """One entry of the hub's detection log, keyed by the clip it belongs to."""

    start_time: int
    end_time: int
    event_type: int
    alarm_type: int


# What the hub answers a multipleRequest with, once unpacked: method name to
# that method's own result body.
HubStatus = dict[str, dict[str, Any]]
