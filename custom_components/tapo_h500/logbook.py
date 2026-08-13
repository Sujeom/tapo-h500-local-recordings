"""Logbook entries, so history reads as prose rather than state changes.

Without this the logbook shows "Front Doorbell Activity changed to
2026-08-13T10:42:25+00:00", which is a timestamp printed twice and says
nothing about what happened. Here the same row reads:

    Front Doorbell  someone rang the doorbell (person)

The description is built from the same decoded codes as everything else, so
the logbook, the cards and a notification never disagree about what an event
was.
"""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import Event, HomeAssistant, callback

from .const import DETECTION_NAMES, DOMAIN, RING_ALARM_TYPES


def _phrase(detection_types: list[int]) -> str:
    """What happened, in the order a person would say it."""
    if not detection_types:
        return "activity"
    if RING_ALARM_TYPES & set(detection_types):
        lead = "someone rang the doorbell"
        # 10 accompanies every press and would read as a contradiction beside
        # it, exactly as it does in describe_detection.
        rest = [DETECTION_NAMES[code] for code in detection_types
                if code in DETECTION_NAMES and code not in (10, 17)]
        return f"{lead} ({', '.join(rest)})" if rest else lead
    named = [DETECTION_NAMES[code] for code in detection_types
             if code in DETECTION_NAMES]
    unknown = [f"type {code}" for code in detection_types
               if code not in DETECTION_NAMES]
    return ", ".join(named + unknown) or "activity"


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict]], None],
) -> None:
    @callback
    def describe(event: Event) -> dict:
        data = event.data or {}
        return {
            "name": data.get("name") or "Camera",
            "message": _phrase(list(data.get("detection_types") or [])),
        }

    async_describe_event(DOMAIN, f"{DOMAIN}_event", describe)
