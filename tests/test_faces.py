"""Face names live on the config entry, and faces_seen groups sightings.

faces_seen is exercised for real against the stubbed coordinator; the service
and the sensor are checked statically, since both need the Home Assistant
runtime. What matters is that one edit reaches every consumer -- the whole
reason names moved off the cards -- and that ids compare as strings, since the
hub reports them as numbers while YAML hands them over as either.
"""
import re
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
# Reuses the Home Assistant stubs that module installs; importing it is what
# makes the real coordinator constructible without Home Assistant present.
from test_coordinator import _build  # noqa: E402

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
INIT = (COMPONENT / "__init__.py").read_text()
SENSOR = (COMPONENT / "sensor.py").read_text()


def _with_faces(coord, clips, names=None):
    coord.cameras = [{"device_id": "cam0", "alias": "Front"}]
    coord.data = {"clips": {0: clips}, "hub": {}}
    coord.entry.options = dict(coord.entry.options)
    if names is not None:
        coord.entry.options["face_names"] = names
    return coord


class NameMap(unittest.TestCase):
    def test_ids_are_compared_as_strings(self):
        """The hub reports numeric ids; YAML and the services API hand over
        either. A map that answers to 123 but not "123" reads as empty."""
        coord, _ = _build()
        _with_faces(coord, [], {123: "Alice"})
        self.assertEqual(coord.face_names, {"123": "Alice"})

    def test_no_map_is_empty_not_an_error(self):
        coord, _ = _build()
        _with_faces(coord, [])
        self.assertEqual(coord.face_names, {})


class Sightings(unittest.TestCase):
    def test_sightings_are_counted_and_the_newest_kept(self):
        coord, _ = _build()
        _with_faces(coord, [
            {"startTime": 300, "event_info": [{"face_id": 7}]},
            {"startTime": 100, "event_info": [{"face_id": 7}, {"face_id": 9}]},
        ], {"7": "Alice"})
        faces = coord.faces_seen()
        self.assertEqual(faces["7"]["sightings"], 2)
        self.assertEqual(faces["7"]["last_seen"], 300)
        self.assertEqual(faces["7"]["name"], "Alice")

    def test_an_unnamed_face_still_appears(self):
        """Naming decides whether someone gets a sensor, not whether the
        integration counts them."""
        coord, _ = _build()
        _with_faces(coord, [{"startTime": 100, "event_info": [{"face_id": 9}]}], {"7": "Alice"})
        faces = coord.faces_seen()
        self.assertIn("9", faces)
        self.assertIsNone(faces["9"]["name"])

    def test_it_records_which_camera_saw_them_last(self):
        """The naming screen builds a photo link from this. Without it there
        is no path to a thumbnail and every face is an unnamed number."""
        coord, _ = _build()
        _with_faces(coord, [{"startTime": 100, "event_info": [{"face_id": 7}]}])
        self.assertEqual(coord.faces_seen()["7"]["camera_index"], 0)

    def test_the_camera_index_follows_the_newest_sighting(self):
        """Not the first one encountered: the photo should be the most recent
        picture of them, matching last_seen."""
        coord, _ = _build()
        coord.cameras = [{"device_id": "a", "alias": "Front"},
                         {"device_id": "b", "alias": "Side"}]
        coord.entry.options = dict(coord.entry.options)
        coord.data = {"clips": {
            0: [{"startTime": 100, "event_info": [{"face_id": 7}]}],
            1: [{"startTime": 900, "event_info": [{"face_id": 7}]}],
        }, "hub": {}}
        face = coord.faces_seen()["7"]
        self.assertEqual(face["last_seen"], 900)
        self.assertEqual(face["camera_index"], 1)

    def test_which_cameras_saw_them(self):
        coord, _ = _build()
        _with_faces(coord, [{"startTime": 100, "event_info": [{"face_id": 7}]}])
        self.assertEqual(coord.faces_seen()["7"]["cameras"], ["Front"])

    def test_no_faces_is_an_empty_map(self):
        coord, _ = _build()
        _with_faces(coord, [{"startTime": 100, "event_info": []}])
        self.assertEqual(coord.faces_seen(), {})


class EventNames(unittest.TestCase):
    """The event resolves ids to names so an automation does not have to."""

    def test_only_named_faces_are_published(self):
        EVENT = (COMPONENT / "event.py").read_text()
        body = EVENT.split("def _known_faces", 1)[1].split("def _own_frame", 1)[0]
        self.assertIn("if str(face) in names", body)

    def test_names_are_sorted_so_two_people_read_the_same_way(self):
        EVENT = (COMPONENT / "event.py").read_text()
        body = EVENT.split("def _known_faces", 1)[1].split("def _own_frame", 1)[0]
        self.assertIn("sorted(", body)

    def test_the_raw_ids_are_still_published_too(self):
        """An automation matching a specific unnamed person still needs them."""
        EVENT = (COMPONENT / "event.py").read_text()
        self.assertIn('"face_ids": face_ids(entry)', EVENT)


class Service(unittest.TestCase):
    def test_naming_writes_to_the_entry_not_a_card(self):
        """The point of the change: one place, read by everything."""
        self.assertIn("async_update_entry", INIT)
        self.assertIn("CONF_FACE_NAMES: names", INIT)

    def test_an_empty_name_clears_rather_than_storing_a_blank(self):
        body = INIT.split("async def name_face", 1)[1].split("for service,", 1)[0]
        self.assertIn("names.pop(face_id, None)", body)

    def test_the_shared_map_is_published_to_callers(self):
        """So a card shows names without being configured with them."""
        self.assertIn('"face_names": coordinator.face_names', INIT)

    def test_a_sensor_exists_per_named_face(self):
        self.assertIn("H500FaceSensor(coordinator, entry, face_id, name)", SENSOR)
        self.assertIn("coordinator.face_names.items()", SENSOR)


if __name__ == "__main__":
    unittest.main()
