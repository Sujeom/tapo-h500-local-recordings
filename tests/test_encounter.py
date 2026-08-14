"""One arrival, however many cameras watched it happen.

Two doorbells covering one path see the same person twice, so a visit event per
camera is two notifications about one arrival -- the exact thing the visit event
exists to stop, reappearing a level up.

Two halves, and both are needed. Cameras rarely index a shared arrival on the
same poll, so merging within one poll catches only the simultaneous case; the
rest is caught by remembering what was just announced. These drive the real
poll for both.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
WINDOW = const.ENCOUNTER_SECONDS
JOURNEY = const.DIRECTION_WINDOW

CAMERAS = [{"device_id": "cam0", "alias": "Gate"},
           {"device_id": "cam1", "alias": "Door"}]


def visit(camera, at, faces=(), recordings=1, detections=(2, 6)):
    return {"camera": camera, "at": at, "face_ids": list(faces),
            "recordings": recordings, "detections": list(detections),
            "names": []}


def mask(*codes):
    value = 0
    for code in codes:
        value |= 1 << (code - 1)
    return value


def clip(when, face=None, events=mask(2, 6)):
    entry = {"startTime": when, "endTime": when + 15, "events_1": events}
    if face is not None:
        entry["event_info"] = [{"face_id": face}]
    return entry


class _Client:
    def __init__(self):
        self.per_camera = {}

    def cameras(self):
        return list(CAMERAS)

    def _for(self, camera):
        return list(self.per_camera.get(camera["alias"], []))

    def recent(self, camera, start, end):
        return self._for(camera)

    def detections(self, camera, start, end):
        return self._for(camera)

    def hub_status(self):
        return {}


def build(names=None):
    client = _Client()
    hass = harness._Hass()
    coord = coordinator_mod.H500Coordinator(
        hass, harness._Entry(20, face_names=names or {}), client)
    coord._download_new = lambda *a, **k: None
    return coord, client, hass


def poll(coord):
    coord.data = asyncio.run(coord._async_update_data())
    return coord.data


def visits(hass):
    return [data for name, data in hass.bus.fired
            if name == const.EVENT_VISIT]


class TheRule(unittest.TestCase):
    def test_simultaneous_cameras_are_one_arrival(self):
        self.assertTrue(clips.same_encounter(
            visit("Gate", NOW - 100), visit("Door", NOW - 90),
            WINDOW, JOURNEY))

    def test_far_apart_strangers_are_two_arrivals(self):
        self.assertFalse(clips.same_encounter(
            visit("Gate", NOW - 300), visit("Door", NOW - 100),
            WINDOW, JOURNEY))

    def test_a_shared_face_stretches_it_to_a_whole_journey(self):
        """Recognised at the gate and again at the door is evidently one
        person walking, where two strangers two minutes apart are not."""
        self.assertTrue(clips.same_encounter(
            visit("Gate", NOW - 200, faces=["77"]),
            visit("Door", NOW - 100, faces=["77"]),
            WINDOW, JOURNEY))

    def test_a_shared_face_does_not_stretch_it_forever(self):
        self.assertFalse(clips.same_encounter(
            visit("Gate", NOW - JOURNEY * 3, faces=["77"]),
            visit("Door", NOW, faces=["77"]),
            WINDOW, JOURNEY))

    def test_different_faces_are_different_people(self):
        self.assertFalse(clips.same_encounter(
            visit("Gate", NOW - 200, faces=["77"]),
            visit("Door", NOW - 100, faces=["88"]),
            WINDOW, JOURNEY))

    def test_one_camera_never_merges_with_itself(self):
        """Its own recordings are already grouped into visits. Re-grouping
        here would swallow a genuine second visitor at the same door."""
        self.assertFalse(clips.same_encounter(
            visit("Gate", NOW - 200, faces=["77"]),
            visit("Gate", NOW - 100, faces=["77"]),
            WINDOW, JOURNEY))


class Grouping(unittest.TestCase):
    def test_two_cameras_become_one_group(self):
        groups = clips.merge_visits(
            [visit("Gate", NOW - 100), visit("Door", NOW - 90)],
            WINDOW, JOURNEY)
        self.assertEqual(len(groups), 1)

    def test_unrelated_visits_stay_apart(self):
        groups = clips.merge_visits(
            [visit("Gate", NOW - 5000), visit("Door", NOW - 100)],
            WINDOW, JOURNEY)
        self.assertEqual(len(groups), 2)

    def test_a_walk_past_several_cameras_stays_one_encounter(self):
        """Compared against the newest visit in the group, not the first, or a
        walk splits as soon as it outruns the window from where it started."""
        walk = [visit("A", NOW - 100), visit("B", NOW - 80),
                visit("C", NOW - 55), visit("D", NOW - 30)]
        self.assertEqual(len(clips.merge_visits(walk, WINDOW, JOURNEY)), 1)

    def test_the_combined_event_names_every_camera(self):
        combined = clips.combine_visits(
            [visit("Door", NOW - 90), visit("Gate", NOW - 100)])
        self.assertEqual(combined["cameras"], ["Door", "Gate"])

    def test_it_is_keyed_on_where_they_were_seen_first(self):
        """Where somebody came from is the useful half of a two-camera
        sighting, and it is not the one that happened to be listed first."""
        combined = clips.combine_visits(
            [visit("Door", NOW - 90), visit("Gate", NOW - 100)])
        self.assertEqual(combined["camera"], "Gate")
        self.assertEqual(combined["at"], NOW - 100)

    def test_what_was_seen_is_the_union(self):
        combined = clips.combine_visits([
            visit("Gate", NOW - 100, faces=["77"], detections=(2, 6)),
            visit("Door", NOW - 90, faces=["88"], detections=(2, 8)),
        ])
        self.assertEqual(combined["detections"], [2, 6, 8])
        self.assertEqual(combined["face_ids"], ["77", "88"])
        self.assertEqual(combined["recordings"], 2)

    def test_the_description_matches_the_merged_codes(self):
        combined = clips.combine_visits([
            visit("Gate", NOW - 100, detections=(2, 6)),
            visit("Door", NOW - 90, detections=(2, 8)),
        ])
        self.assertIn("vehicle", combined["detection"])
        self.assertIn("person", combined["detection"])

    def test_a_lone_visit_still_carries_a_camera_list(self):
        """So an automation reading trigger.event.data.cameras never has to
        care whether one camera or two saw it."""
        combined = clips.combine_visits([visit("Gate", NOW - 100)])
        self.assertEqual(combined["cameras"], ["Gate"])


class InThePoll(unittest.TestCase):
    def test_both_cameras_on_one_poll_announce_once(self):
        coord, client, hass = build()
        poll(coord)
        client.per_camera["Gate"] = [clip(NOW - 100)]
        client.per_camera["Door"] = [clip(NOW - 90)]
        poll(coord)
        self.assertEqual(len(visits(hass)), 1)
        self.assertEqual(visits(hass)[0]["cameras"], ["Door", "Gate"])

    def test_the_second_camera_arriving_a_poll_later_announces_nothing(self):
        """The common case: at two seconds apart the two cameras index the
        same arrival on consecutive polls, so merging within one poll catches
        only half of it."""
        coord, client, hass = build()
        poll(coord)
        client.per_camera["Gate"] = [clip(NOW - 100)]
        poll(coord)
        client.per_camera["Door"] = [clip(NOW - 90)]
        poll(coord)
        self.assertEqual(len(visits(hass)), 1)

    def test_a_genuinely_separate_visitor_still_announces(self):
        """Suppression must not be a mute button on the second camera."""
        coord, client, hass = build()
        poll(coord)
        client.per_camera["Gate"] = [clip(NOW - 5000)]
        poll(coord)
        client.per_camera["Door"] = [clip(NOW - 60)]
        poll(coord)
        self.assertEqual(len(visits(hass)), 2)

    def test_a_recognised_person_walking_between_them_announces_once(self):
        coord, client, hass = build({"77": "Alice"})
        poll(coord)
        client.per_camera["Gate"] = [clip(NOW - 150, face=77,
                                          events=mask(2, 6, 20))]
        poll(coord)
        client.per_camera["Door"] = [clip(NOW - 60, face=77,
                                          events=mask(2, 6, 20))]
        poll(coord)
        self.assertEqual(len(visits(hass)), 1)

    def test_two_different_people_at_the_two_cameras_both_announce(self):
        coord, client, hass = build()
        poll(coord)
        client.per_camera["Gate"] = [clip(NOW - 150, face=77,
                                          events=mask(2, 6, 20))]
        poll(coord)
        client.per_camera["Door"] = [clip(NOW - 60, face=88,
                                          events=mask(2, 6, 20))]
        poll(coord)
        self.assertEqual(len(visits(hass)), 2)

    def test_the_memory_does_not_grow_without_bound(self):
        """It is pruned rather than kept: nothing older than the longest
        window either rule looks at can suppress anything."""
        coord, client, hass = build()
        poll(coord)
        for step in range(6):
            client.per_camera["Gate"] = [clip(NOW - 5000 + step * 1000)]
            poll(coord)
        self.assertLessEqual(len(coord._encounters), 2)


if __name__ == "__main__":
    unittest.main()
