"""One notification per visitor, not one per recording.

The hub reports moments rather than presence: somebody at the door for four
minutes arrives as a string of fifteen-second clips. An automation wired to the
detection event therefore sends sixteen notifications about one person, and
nothing in the integration was announcing the grouping that sessions() has been
doing for the loitering sensor since it was written.

These drive the real poll and count what reached the bus.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
clips_mod = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000


def mask(*codes):
    """events_1 for these alarm codes. Code N is bit N-1."""
    value = 0
    for code in codes:
        value |= 1 << (code - 1)
    return value


PERSON = mask(2, 6)
MOTION = mask(2)


def clip(when, seconds=15, events=PERSON, face=None):
    entry = {"startTime": when, "endTime": when + seconds, "events_1": events}
    if face is not None:
        entry["event_info"] = [{"face_id": face}]
    return entry


class _Client:
    """Hands back whatever this test queued, per camera index."""

    def __init__(self, cameras=None):
        self.per_camera = {}
        self._cameras = cameras or [{"device_id": "cam0", "alias": "Front"}]

    def cameras(self):
        return list(self._cameras)

    def _for(self, camera):
        return list(self.per_camera.get(camera["alias"], []))

    def recent(self, camera, start, end):
        return self._for(camera)

    def detections(self, camera, start, end):
        return self._for(camera)

    def hub_status(self):
        return {}


def build(names=None, cameras=None):
    client = _Client(cameras)
    entry = harness._Entry(20, face_names=names or {})
    hass = harness._Hass()
    coord = coordinator_mod.H500Coordinator(hass, entry, client)
    coord._download_new = lambda *a, **k: None
    return coord, client, hass


def poll(coord):
    coord.data = asyncio.run(coord._async_update_data())
    return coord.data


def announce(coord, clips, index=0):
    """Run the visit check directly, with nothing swallowing its errors.

    _poll wraps it in a broad except so a bad recording can never cost a poll,
    which is right in production and useless here: a mutation that made the
    check raise would read as "no visits announced".
    """
    coord._primed = True
    coord._note_visits({index: clips})


def visits(hass):
    return [data for name, data in hass.bus.fired
            if name == const.EVENT_VISIT]


class OnePerVisit(unittest.TestCase):
    def test_a_string_of_clips_is_one_visit(self):
        """The whole point. Four minutes at the door is sixteen recordings."""
        coord, _, hass = build()
        poll(coord)
        run = [clip(NOW - 240 + step * 15) for step in range(16)]
        # Delivered the way a poll every two seconds delivers them: a few more
        # each time, never all at once.
        for count in range(1, len(run) + 1):
            announce(coord, run[:count])
        self.assertEqual(len(visits(hass)), 1)

    def test_a_second_visitor_later_is_a_second_visit(self):
        coord, _, hass = build()
        poll(coord)
        first = [clip(NOW - 3600)]
        announce(coord, first)
        announce(coord, first + [clip(NOW - 60)])
        self.assertEqual(len(visits(hass)), 2)

    def test_the_gap_is_what_separates_them(self):
        """Two recordings inside the gap are one visit; outside it, two."""
        coord, _, hass = build()
        poll(coord)
        gap = const.LOITER_GAP
        together = [clip(NOW - 400), clip(NOW - 400 + gap - 20)]
        announce(coord, together[:1])
        announce(coord, together)
        self.assertEqual(len(visits(hass)), 1)

        apart = together + [clip(NOW - 400 + gap * 3)]
        announce(coord, apart)
        self.assertEqual(len(visits(hass)), 2)

    def test_a_restart_does_not_replay_the_day(self):
        """The window holds 24 hours. Without the priming guard, restarting at
        teatime announces every visit since breakfast in one burst."""
        coord, client, hass = build()
        client.per_camera["Front"] = [clip(NOW - 3600), clip(NOW - 1800),
                                      clip(NOW - 600)]
        poll(coord)
        poll(coord)
        poll(coord)
        self.assertEqual(visits(hass), [])

    def test_a_late_clip_does_not_re_announce_a_visit(self):
        """The hub can index a recording after one that started later. An
        older visit turning up in the window is history, not news."""
        coord, _, hass = build()
        poll(coord)
        announce(coord, [clip(NOW - 60)])
        announce(coord, [clip(NOW - 7200), clip(NOW - 60)])
        self.assertEqual(len(visits(hass)), 1)

    def test_each_camera_keeps_its_own_place(self):
        """One camera being busy must not silence another.

        The side camera's visit is deliberately the OLDER of the two. A single
        shared marker would be set by the front camera first and would then
        read the side camera's visit as history, which is exactly the bug --
        and two visits at the same moment cannot tell that apart, because both
        are new to an empty marker whichever way it is stored.
        """
        coord, _, hass = build(cameras=[
            {"device_id": "cam0", "alias": "Front"},
            {"device_id": "cam1", "alias": "Side"}])
        poll(coord)
        coord._primed = True
        both = {0: [clip(NOW - 60)], 1: [clip(NOW - 600)]}
        coord._note_visits(both)
        # Polled again with nothing new. A marker written under the wrong key
        # leaves the camera it belonged to permanently unrecorded, so its visit
        # is announced again on every poll -- which one pass cannot show.
        coord._note_visits(both)
        self.assertEqual(len(visits(hass)), 2)
        by_camera = {data["camera"]: data for data in visits(hass)}
        self.assertEqual(sorted(by_camera), ["Front", "Side"])
        # ...and each one says which camera it came from, rather than every
        # event claiming to be the first camera.
        self.assertEqual(by_camera["Front"]["camera_index"], 0)
        self.assertEqual(by_camera["Side"]["camera_index"], 1)


class Payload(unittest.TestCase):
    def test_it_says_where_and_when(self):
        coord, _, hass = build()
        poll(coord)
        announce(coord, [clip(NOW - 60)])
        data = visits(hass)[0]
        self.assertEqual(data["camera"], "Front")
        self.assertEqual(data["camera_index"], 0)
        self.assertEqual(data["at"], NOW - 60)
        self.assertEqual(data["entry_id"], "test")

    def test_it_says_what_the_hub_saw(self):
        coord, _, hass = build()
        poll(coord)
        announce(coord, [clip(NOW - 60, events=mask(2, 6, 8))])
        data = visits(hass)[0]
        self.assertEqual(data["detections"], [2, 6, 8])
        self.assertIn("vehicle", data["detection"])

    def test_it_names_a_face_that_has_a_name(self):
        coord, _, hass = build({"77": "Alice"})
        poll(coord)
        announce(coord, [clip(NOW - 60, events=mask(2, 6, 20), face=77)])
        data = visits(hass)[0]
        self.assertEqual(data["names"], ["Alice"])
        self.assertEqual(data["face_ids"], ["77"])

    def test_an_unnamed_face_leaves_the_names_empty(self):
        """Empty means "nobody the hub matched to a name", which is the
        ordinary case, and is not the same as nobody being there."""
        coord, _, hass = build({})
        poll(coord)
        announce(coord, [clip(NOW - 60, events=mask(2, 6, 22), face=9001)])
        data = visits(hass)[0]
        self.assertEqual(data["names"], [])
        self.assertEqual(data["face_ids"], ["9001"])

    def test_only_recordings_inside_the_visit_are_described(self):
        """An earlier visit's codes must not leak into this one's summary."""
        coord, _, hass = build()
        poll(coord)
        announce(coord, [clip(NOW - 7200, events=mask(2, 8)),
                         clip(NOW - 60, events=mask(2, 6))])
        data = visits(hass)[-1]
        self.assertNotIn(8, data["detections"])


class Describe(unittest.TestCase):
    """describe_codes is what lets a visit be described at all -- it spans
    several recordings, so there is no single entry to hand over."""

    def test_it_matches_the_per_recording_wording(self):
        entry = {"events_1": mask(2, 6, 9)}
        self.assertEqual(clips_mod.describe_codes([2, 6, 9]),
                         clips_mod.describe_detection(entry))

    def test_it_still_collapses_a_missed_press(self):
        text = clips_mod.describe_codes([2, 10, 17])
        self.assertIn("doorbell (missed)", text)
        self.assertNotIn("missed doorbell +", text)

    def test_nothing_at_all_describes_as_nothing(self):
        self.assertIsNone(clips_mod.describe_codes([]))


if __name__ == "__main__":
    unittest.main()
