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

from .clips import (
    describe_detection, distinct, highlights, start_of, summarise,
)
from .const import (
    CONF_NIGHT_END, CONF_NIGHT_START, DATA_HUBS, DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START, DOMAIN, LOOKBACK_SECONDS,
)

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
        # Names and their clips, kept as a list until the names are known to
        # be distinct. Two hubs can each have a "Front Doorbell", and putting
        # those straight into a dictionary drops one without a word -- the
        # answer then describes half the house as though it were all of it.
        found: list[tuple[str, str, list[dict]]] = []
        for coordinator in _hubs(hass):
            for index, camera in enumerate(coordinator.cameras):
                found.append((camera.get("alias") or f"Camera {index}",
                              coordinator.entry.title,
                              coordinator.clips_for(index)))
        labels = distinct([(name, hub) for name, hub, _ in found])
        per_camera = {label: clips
                      for label, (_, _, clips) in zip(labels, found)}

        response = request.create_response()
        if not per_camera:
            response.async_set_speech("No cameras are set up.")
            return response
        # What was different first, because that is the answer, and the counts
        # after it. Spoken aloud a bare list of totals is the same sentence
        # every day, and the day worth mentioning sounds exactly like the day
        # that was not.
        spoken = summarise(per_camera, now)
        # This answer covers every hub at once and "after dark" is configured
        # per hub, so it takes the first hub's night window. Two hubs in one
        # house with different ideas of night is not a real arrangement, and
        # the alternative is ignoring the setting altogether.
        options = _hubs(hass)[0].entry.options
        notable = highlights(
            per_camera, now, LOOKBACK_SECONDS,
            options.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
            options.get(CONF_NIGHT_END, DEFAULT_NIGHT_END))
        if notable:
            spoken = f"{'. '.join(notable)}. {spoken}"
        response.async_set_speech(spoken)
        return response


async def async_setup_intents(hass: HomeAssistant) -> None:
    intent.async_register(hass, LastEventIntent())
    intent.async_register(hass, TodayIntent())
