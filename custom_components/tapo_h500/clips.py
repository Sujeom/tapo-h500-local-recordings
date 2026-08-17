"""Interpreting what the hub says about clips and detections.

Pure functions over the hub's response dictionaries. No network, no
filesystem, no Home Assistant — so this is the part that can be tested
without a hub or an installed Home Assistant.
"""
from __future__ import annotations

import re

from .const import (
    DETECTION_NAMES, EVENT_MOTION, EVENT_RING, LOITER_GAP, LOITER_SECONDS,
    RING_ALARM_TYPES, RING_HINTS, TAMPER_CODES, UNUSUAL_FLOOR,
    UNUSUAL_MULTIPLIER,
)

# Fields the hub has been seen to label activity with, most specific first.
TYPE_FIELDS = ("video_type", "detection_type", "event_type", "type")


def camera_slug(camera: dict) -> str:
    """A filesystem-safe token for a camera's alias."""
    alias = camera.get("alias") or camera.get("device_name") or "camera"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(alias)).strip("_.").lower()
    return (slug or "camera")[:60]


def distinct(labels: list[tuple[str, str]]) -> list[str]:
    """Display names for (name, qualifier) pairs, qualifying only the clashes.

    Two hubs can each have a camera called "Front Doorbell", and so can one
    hub if somebody names them that way. Anything keyed on the name alone --
    a summary, a spoken answer -- silently drops one of them and reads as
    though only one camera exists.

    Qualifying every name would put a hub address into every spoken sentence,
    so only names that actually appear twice are qualified. Both of them are,
    not just the second: "Front Doorbell" and "Front Doorbell (192.168.11.5)"
    side by side is worse than neither being bare, because the first looks
    like the real one.
    """
    counts: dict[str, int] = {}
    for name, _ in labels:
        counts[name] = counts.get(name, 0) + 1
    return [f"{name} ({qualifier})" if counts[name] > 1 else name
            for name, qualifier in labels]


def clashing_names(every_camera: list[dict], mine: list[dict]) -> list[str]:
    """Folder names more than one camera would write to, limited to `mine`.

    Downloads are filed under a slug of the camera's own name, which is what
    makes "already downloaded" a check of the files on disk rather than a
    separate index that could disagree with them. It also means two cameras
    called the same thing share a folder, and that question is then answered
    for one camera by the other's recording. Two hubs make that likely rather
    than theoretical.

    Compared as slugs, not aliases: "Front Door" and "front-door" look
    different in the app and are the same directory on disk.

    Limited to `mine` so a warning names something the person reading it can
    actually find.
    """
    counts: dict[str, int] = {}
    for camera in every_camera:
        slug = camera_slug(camera)
        counts[slug] = counts.get(slug, 0) + 1
    ours = {camera_slug(camera) for camera in mine}
    return sorted(slug for slug, count in counts.items()
                  if count > 1 and slug in ours)


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
    """A short phrase for what the hub says triggered this recording."""
    return describe_codes(detection_types(entry))


def describe_codes(types: list[int]) -> str | None:
    """The same phrase, from codes rather than from a recording.

    Split out because a visit spans several recordings and its description is
    the union of their codes -- there is no single entry to hand over. Building
    a fake one with a hand-made events_1 mask was the alternative, and getting
    that mask wrong is a mistake this codebase has already made once.

    Unnamed codes are shown as their number rather than guessed at, so an
    unfamiliar code reads as "type 22" instead of silently becoming "motion".
    """
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


def newest_matching(clips: list[dict], matches, keep: int) -> set[int]:
    """Start times of the newest `keep` recordings this predicate accepts.

    What retention must leave alone however old it gets. One number for
    everything meant a busy afternoon of motion could evict the doorbell press
    that was the whole reason for keeping anything, and it went silently.

    Sorted explicitly. The hub promises no order for searchVideoWithUTC, and
    slicing the list as it arrived protected whichever recordings happened to
    come back first -- which on a hub answering oldest-first is exactly the
    ones about to be deleted anyway, so the protection did nothing.

    An empty set for keep <= 0, which is how "no special treatment" is
    expressed. The caller subtracts this from what it deletes, so an
    off-by-one here loses recordings.
    """
    if keep <= 0:
        return set()
    moments = sorted(
        (moment for clip in clips
         for moment in [start_of(clip)]
         if moment is not None and matches(clip)),
        reverse=True)
    return set(moments[:keep])


def has_detection(entry: dict, codes: set[int]) -> bool:
    """Whether any of these alarm codes fired on this recording."""
    return bool(codes & set(detection_types(entry)))


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


def unusual_threshold(clips: list[dict], now: int, window: int,
                      multiplier: float, floor: int) -> float:
    """How many events in an hour would count as standing out, for this camera.

    Two guards, because a ratio alone is useless at both ends. A camera that
    normally sees nothing has a baseline of zero, and any event at all would
    be infinitely unusual -- hence the floor, below which nothing is flagged.
    And a camera that is always busy should not flag continuously, which is
    what the multiplier is for.

    One expression, deliberately: an earlier version also returned early below
    the floor, which enforced it twice. Removing either guard then changed no
    behaviour, so no test could tell a broken one from a working one -- the
    floor lives here and only here.
    """
    return max(floor, hourly_baseline(clips, now, window) * multiplier)


def unusually_busy(clips: list[dict], now: int, window: int,
                   multiplier: float, floor: int) -> bool:
    """Whether the last hour stands out against this camera's own baseline."""
    return events_since(clips, now - 3600) >= unusual_threshold(
        clips, now, window, multiplier, floor)


# What each level means, in the order they escalate. Exported so the entity's
# `options` and the translations cannot drift from the function.
ACTIVITY_LEVELS = ("quiet", "active", "busy", "unusual")


def activity_level(clips: list[dict], now: int, window: int,
                   multiplier: float, floor: int) -> str:
    """The last hour in one word, rather than in three separate booleans.

    "Is anything happening" currently means reading a recording count, an
    unusual-activity flag and a last-activity timestamp and joining them up by
    eye. This is the join, done once and named.

    The busy step is derived rather than given numbers of its own: it is
    exactly halfway to unusual, on both guards at once. Two independent pairs
    of numbers could be set so that a camera was busy but not unusual at four
    events and unusual but not busy at five, and a scale that goes backwards is
    worse than no scale.
    """
    recent = events_since(clips, now - 3600)
    if not recent:
        return "quiet"
    threshold = unusual_threshold(clips, now, window, multiplier, floor)
    if recent >= threshold:
        return "unusual"
    if recent >= threshold / 2:
        return "busy"
    return "active"


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


def last_visit(clips: list[dict], gap: int, matches) -> tuple[int, int] | None:
    """The most recent visit whose recordings this predicate accepts.

    (first sighting, last sighting), or None if there were none.
    """
    visits = sessions([clip for clip in clips if matches(clip)], gap)
    if not visits:
        return None
    start, end, _ = visits[-1]
    return (start, end)


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
    visit = last_visit(clips, gap, lambda clip: has_detection(clip, {22}))
    if visit is None:
        return 0
    start, end = visit
    # Over. Someone who left two hours ago is not loitering now.
    if now - end > gap:
        return 0
    lasted = end - start
    return lasted if lasted >= minimum else 0


def likely_delivery(clips: list[dict], now: int, gap: int, longest: int,
                    hold: int, hour: int, night_start: int,
                    night_end: int) -> bool:
    """Whether the visit that just ended reads like a delivery.

    Three things at once: somebody was there, the hub did not recognise them,
    and they did not stay. In daylight, that is a courier far more often than
    it is anything else.

    Retrospective on purpose, and this is the part worth understanding. The
    length of a visit is not known while it is happening -- at the moment the
    hub reports a detection, the person has been there for one clip, and so
    has everybody who is about to stay for ten minutes. So this cannot answer
    "is the thing at my door right now a delivery"; it answers "was that a
    delivery", once the visit is over, and stays true for a while afterwards
    so an automation has time to see it.

    A guess, and named like one. Nothing the hub reports says "courier". A
    canvasser looks identical, and so does somebody checking whether the house
    is empty -- which is why this is a signal to describe an afternoon with,
    not a reason to stay quiet.
    """
    if in_night(hour, night_start, night_end):
        return False
    visit = last_visit(clips, gap,
                       lambda clip: has_detection(clip, {6}))
    if visit is None:
        return False
    start, end = visit
    # Still happening, so its length is not final yet.
    if now - end <= gap:
        return False
    # Over long enough ago to no longer be news.
    if now - end > hold:
        return False
    if end - start > longest:
        return False
    # Recognised at any point during the visit. Somebody the hub knows,
    # arriving and leaving quickly, is a member of the household in a hurry.
    return not any(has_detection(clip, {20}) for clip in clips
                   if start <= (start_of(clip) or -1) <= end)


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


def _clock(hour: int) -> str:
    """An hour of the day as people say it, for a sentence rather than a chart."""
    if hour == 0:
        return "midnight"
    if hour == 12:
        return "midday"
    return f"{hour % 12 or 12}{'am' if hour < 12 else 'pm'}"


def highlights(per_camera: dict[str, list[dict]], now: int, window: int,
               night_start: int, night_end: int) -> list[str]:
    """What was different about the day, rather than what was in it.

    `summarise` counts, which is the honest thing to do and not what anyone
    reads a digest for: "Front: 48 recordings (12 person, 3 vehicle)" is the
    same sentence every day, and a day worth knowing about looks exactly like
    a day that was not.

    So this reports only the things a day can have that most days do not, and
    returns nothing at all when there were none -- an empty list is the common
    case and is the point. Every one of them is computed from the same polled
    window as everything else here, which is a day; none of it is a comparison
    against last week, because there is no last week to compare against.

    Ordered by how much they matter rather than by camera. A camera reporting
    tampering goes first however far down the list its name would put it.
    """
    lines: list[str] = []
    quiet: list[str] = []
    for name, clips in per_camera.items():
        recent = [clip for clip in clips
                  if (start_of(clip) or 0) >= now - window]
        if not recent:
            quiet.append(name)
            continue
        if any(has_detection(clip, TAMPER_CODES) for clip in recent):
            lines.insert(0, f"{name} reported being tampered with")
        hours = hourly_counts(recent)
        peak = max(hours)
        # Against a flat day rather than against the hours that had anything
        # in them: a camera that saw ten people in one hour and nothing else
        # had a peak, and dividing by "hours with activity" would hide it.
        if peak >= max(UNUSUAL_FLOOR, len(recent) / 24 * UNUSUAL_MULTIPLIER):
            lines.append(
                f"{name} was busiest around {_clock(hours.index(peak))} "
                f"({peak} recordings)")
        after_dark = sum(1 for clip in recent
                         if 22 in detection_types(clip)
                         and in_night(local_hour(start_of(clip)),
                                      night_start, night_end))
        if after_dark:
            lines.append(f"{after_dark} unfamiliar face"
                         f"{'s' if after_dark != 1 else ''} at {name} "
                         f"after dark")
        longest = longest_visit(recent, LOITER_GAP)
        if longest >= LOITER_SECONDS:
            lines.append(f"somebody was at {name} for "
                         f"{round(longest / 60)} minutes")
    if quiet:
        lines.append(f"{', '.join(sorted(quiet))} recorded nothing")
    return lines


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


def same_encounter(earlier: dict, later: dict, window: int,
                   journey: int) -> bool:
    """Whether two cameras' visits are one person arriving once.

    Two doorbells covering one path see the same arrival twice, and an event
    per camera is two notifications about one person -- exactly what the visit
    event exists to stop, reappearing a level up.

    Two rules, both needed. Within `window` the visits are simultaneous enough
    to be the same thing whoever it was; beyond that a shared face id is
    required as well, because a person recognised at the gate and again at the
    door is evidently one journey where two strangers two minutes apart at
    different cameras are evidently not.

    Never within one camera. Its own recordings are already grouped into visits
    by `sessions`, and re-grouping them here would swallow a real second
    visitor.
    """
    if earlier.get("camera") == later.get("camera"):
        return False
    gap = later["at"] - earlier["at"]
    if gap <= window:
        return True
    return gap <= journey and bool(
        set(earlier.get("face_ids") or []) & set(later.get("face_ids") or []))


def merge_visits(visits: list[dict], window: int,
                 journey: int) -> list[list[dict]]:
    """Group per-camera visits that are one arrival, oldest group first.

    Compared against the newest visit already in the group rather than against
    the first, so a walk past four cameras stays one encounter instead of
    splitting once it outruns the window from where it started.
    """
    groups: list[list[dict]] = []
    for visit in sorted(visits, key=lambda entry: entry["at"]):
        for group in groups:
            if any(same_encounter(member, visit, window, journey)
                   for member in group):
                group.append(visit)
                break
        else:
            groups.append([visit])
    return groups


def combine_visits(group: list[dict]) -> dict:
    """One event from several cameras' views of one arrival.

    Keyed on the earliest, because where somebody was seen FIRST is where they
    came from, which is the useful half of a two-camera sighting.
    """
    first = min(group, key=lambda entry: entry["at"])
    codes = sorted({code for entry in group
                    for code in entry.get("detections") or []})
    return {
        **first,
        "cameras": sorted({entry["camera"] for entry in group}),
        "recordings": sum(entry.get("recordings", 0) for entry in group),
        "detections": codes,
        "detection": describe_codes(codes),
        "face_ids": sorted({face for entry in group
                            for face in entry.get("face_ids") or []}),
        "names": sorted({name for entry in group
                         for name in entry.get("names") or []}),
    }


def suggest_ranks(trails: list[list[dict]], window: int) -> dict[str, int]:
    """Where the cameras sit between the street and the door, inferred.

    The layout screen asks the one question the integration claimed it could
    not answer -- and the data to answer it has been sitting in the face trails
    all along. People arrive from the street and walk towards the door, so the
    camera they are usually seen at FIRST is the one nearer the street.

    Each hop from one camera to another within `window` counts once: a point
    against the camera left and a point for the camera reached. A gate ends up
    deeply negative and a doorbell positive, and sorting by that score is the
    order. Ties break on the name so the answer does not depend on dictionary
    ordering.

    Empty when nobody has been seen crossing between two cameras -- which
    matters, because this becomes the default on a form and "approaching the
    door" is what people wire a siren to. It falls out of the counting rather
    than being guarded for: no hops means no scores means no ranks. An explicit
    early return said the same thing twice and could not be broken by any
    change a test would notice.

    Each trail is newest first, as faces_seen builds them, and is walked
    backwards here -- reading them in the order they arrive would count every
    journey as going the other way, which produces a confidently reversed
    layout rather than an obviously broken one.
    """
    moves: dict[tuple[str, str], int] = {}
    for trail in trails:
        hops = [hop for hop in reversed(trail)
                if hop.get("camera") and hop.get("at") is not None]
        for before, after in zip(hops, hops[1:]):
            if before["camera"] == after["camera"]:
                continue
            if after["at"] - before["at"] > window:
                continue
            step = (before["camera"], after["camera"])
            moves[step] = moves.get(step, 0) + 1
    score: dict[str, int] = {}
    for (before, after), count in moves.items():
        score[before] = score.get(before, 0) - count
        score[after] = score.get(after, 0) + count
    order = sorted(score, key=lambda camera: (score[camera], camera))
    return {camera: rank for rank, camera in enumerate(order)}


def prowling(trail: list[dict], window: int) -> bool:
    """Whether a trail reads as going round the house rather than through it.

    `trail` is newest first, as faces_seen builds it.

    Somebody arriving passes each camera once: gate, then door. Somebody
    circling comes back to one they have already been past. That return is
    the entire signal, and it is the one thing here that needs no camera
    ranks -- it does not matter which camera is nearer the street, only that
    the same place was reached twice with somewhere else in between.

    Works with two cameras, which matters: front, side, front is a circuit,
    and requiring three distinct places would make this useless on the
    hardware it was written for.

    Consecutive sightings at one camera are collapsed first. Standing in front
    of the front door long enough for two clips is not a lap of the house.
    """
    recent = [hop for hop in trail
              if hop.get("at") is not None and hop.get("camera")]
    if not recent:
        return False
    newest = recent[0]["at"]
    cameras = [hop["camera"] for hop in recent if newest - hop["at"] <= window]
    path = [camera for position, camera in enumerate(cameras)
            if position == 0 or camera != cameras[position - 1]]
    # A repeat in the collapsed path is a return, and a return implies both
    # two distinct cameras and three hops -- so those are not checked
    # separately. Guards that cannot fire are guards no test can protect.
    return len(path) > len(set(path))


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


def hourly_counts(clips: list[dict]) -> list[int]:
    """Recordings per local hour of the day, as 24 numbers starting at midnight.

    The shape of a day rather than a total. "40 recordings" says nothing about
    whether the camera was busy all afternoon or for ten minutes at 3am, and
    that difference is the whole question anyone looks at a doorbell log to
    answer.

    Local hours, not UTC. An hour-of-day chart in the wrong zone is not
    slightly wrong, it is a chart of somebody else's day.
    """
    hours = [0] * 24
    for clip in clips:
        moment = start_of(clip)
        if moment is None:
            continue
        hours[local_hour(moment)] += 1
    return hours


def busiest_hour(clips: list[dict]) -> int | None:
    """The local hour with the most recordings, or None if there are none.

    Ties go to the earlier hour. Deliberately: a quiet camera has many hours
    holding one event each, and picking the latest would make the answer jump
    around as the day filled up.
    """
    hours = hourly_counts(clips)
    peak = max(hours)
    return hours.index(peak) if peak else None


def longest_visit(clips: list[dict], gap: int) -> int:
    """How long the longest visit in the window lasted, in seconds.

    First sighting to last, like every other duration here, so a single
    fifteen-second clip is evidence of fifteen seconds. Zero when there is
    nothing, rather than None: this is read into a template beside a count.
    """
    visits = sessions(clips, gap)
    return max((end - start for start, end, _ in visits), default=0)


def _local(moment: int):
    from homeassistant.util import dt as dt_util
    return dt_util.as_local(dt_util.utc_from_timestamp(moment))


def expected_since(clips: list[dict], since: int, now: int,
                   window: int) -> float:
    """How many events history predicted between `since` and now.

    The watchdog question is not "how long has this camera been quiet" -- a
    fixed answer to that called yesterday's dead doorbell healthy for half a
    day, because the only safe fixed number has to forgive a whole night.
    The question is "how much does its own history say should have happened
    by now": silence across hours that never record accrues nothing, and
    silence across the busy afternoon accrues fast. A camera doing 25 a day
    reads as broken within hours; the back gate doing 2 stays patient.

    Hour-of-day rates from the same local-hour shape the visits sensor uses,
    scaled to per-day by the window the clips were drawn from, then summed
    over each local hour the silence has crossed, pro rata for partials.
    """
    if window <= 0:
        return 0.0
    counts = hourly_counts(clips)
    if not any(counts):
        # Not for the arithmetic -- the walk below would sum zeros -- but to
        # bound it: a pathological `since` far in the past must not spin
        # through half a million empty hour slices. (Backwards time needs no
        # guard at all: the walk simply never runs.)
        return 0.0
    days = window / 86400
    expected = 0.0
    moment = since
    while moment < now:
        slice_end = min(now, (moment // 3600 + 1) * 3600)
        expected += (counts[local_hour(int(moment))] / days
                     * (slice_end - moment) / 3600)
        moment = slice_end
    return expected


def local_hour(moment: int) -> int:
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
