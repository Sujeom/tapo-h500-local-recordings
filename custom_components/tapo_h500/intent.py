"""Assist intents: asking the house what it saw.

Two questions people actually ask out loud, answered from data already in
memory so neither costs a hub request:

    "who was at the door"       -> the newest event, and how long ago
    "what happened today"       -> one sentence per camera

The phrasing comes from the same helpers the cards and the digest use, so a
spoken answer and a notification cannot describe the same event differently.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .clips import describe_detection, start_of, summarise
from .const import DATA_HUBS, DOMAIN

INTENT_LAST_EVENT = "TapoH500LastEvent"
INTENT_TODAY = "TapoH500Today"


def _hubs(hass: HomeAssistant):
    return list((hass.data.get(DOMAIN, {}).get(DATA_HUBS, {})).values())


def _ago(seconds: int) -> str:
    """Spoken duration. Rounded, because "4,133 seconds ago" is not an answer."""
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


class LastEventIntent(intent.IntentHandler):
    """The newest event on any camera."""

    intent_type = INTENT_LAST_EVENT

    async def async_handle(self, request: intent.Intent) -> intent.IntentResponse:
        hass = request.hass
        newest = None
        where = None
        for coordinator in _hubs(hass):
            for index, camera in enumerate(coordinator.cameras):
                for clip in coordinator.clips_for(index):
                    moment = start_of(clip)
                    if moment is None:
                        continue
                    if newest is None or moment > start_of(newest):
                        newest, where = clip, camera.get("alias") or "a camera"

        response = request.create_response()
        if newest is None:
            response.async_set_speech(
                "Nothing has been recorded in the last day.")
            return response

        from homeassistant.util import dt as dt_util
        age = int(dt_util.utcnow().timestamp()) - start_of(newest)
        what = describe_detection(newest) or "activity"
        response.async_set_speech(f"{what} at the {where}, {_ago(age)}.")
        return response


class TodayIntent(intent.IntentHandler):
    """A sentence per camera for the last day."""

    intent_type = INTENT_TODAY

    async def async_handle(self, request: intent.Intent) -> intent.IntentResponse:
        hass = request.hass
        from homeassistant.util import dt as dt_util
        now = int(dt_util.utcnow().timestamp())
        per_camera: dict[str, list[dict]] = {}
        for coordinator in _hubs(hass):
            for index, camera in enumerate(coordinator.cameras):
                name = camera.get("alias") or f"Camera {index}"
                per_camera[name] = coordinator.clips_for(index)

        response = request.create_response()
        response.async_set_speech(summarise(per_camera, now) if per_camera
                                  else "No cameras are set up.")
        return response


async def async_setup_intents(hass: HomeAssistant) -> None:
    intent.async_register(hass, LastEventIntent())
    intent.async_register(hass, TodayIntent())
