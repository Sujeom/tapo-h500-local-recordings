"""Face names live on the config entry, and faces_seen groups sightings.

faces_seen is exercised for real against the stubbed coordinator; the service
and the sensor are checked statically, since both need the Home Assistant
runtime. What matters is that one edit reaches every consumer -- the whole
reason names moved off the cards -- and that ids compare as strings, since the
hub reports them as numbers while YAML hands them over as either.
"""
import importlib
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


class Tracking(unittest.TestCase):
    """Following one person between cameras.

    Real rather than inferred: measured on this hub, face ids are hub-wide and
    two of six ids appeared on both doorbells, so the same number follows one
    person from door to door.
    """

    def _two_cameras(self, clips_by_camera):
        coord, _ = _build()
        coord.cameras = [{"device_id": "a", "alias": "Front"},
                         {"device_id": "b", "alias": "Side"}]
        coord.entry.options = dict(coord.entry.options)
        coord.data = {"clips": clips_by_camera, "hub": {}}
        return coord

    def test_the_trail_follows_one_person_between_cameras(self):
        coord = self._two_cameras({
            0: [{"startTime": 100, "event_info": [{"face_id": 7}]}],
            1: [{"startTime": 500, "event_info": [{"face_id": 7}]}],
        })
        face = coord.faces_seen()["7"]
        self.assertEqual([hop["camera"] for hop in face["trail"]],
                         ["Side", "Front"])

    def test_where_they_were_last_seen(self):
        coord = self._two_cameras({
            0: [{"startTime": 900, "event_info": [{"face_id": 7}]}],
            1: [{"startTime": 100, "event_info": [{"face_id": 7}]}],
        })
        self.assertEqual(coord.faces_seen()["7"]["last_camera"], "Front")

    def test_the_trail_is_newest_first(self):
        coord = self._two_cameras({
            0: [{"startTime": 100, "event_info": [{"face_id": 7}]},
                {"startTime": 300, "event_info": [{"face_id": 7}]}],
            1: [],
        })
        moments = [hop["at"] for hop in coord.faces_seen()["7"]["trail"]]
        self.assertEqual(moments, sorted(moments, reverse=True))

    def test_the_trail_is_capped(self):
        """It is written to the state machine on every update, so it cannot
        grow with the poll window."""
        const = importlib.import_module("tapo_h500.const")
        many = [{"startTime": 100 + n, "event_info": [{"face_id": 7}]}
                for n in range(const.FACE_TRAIL_MAX + 15)]
        coord = self._two_cameras({0: many, 1: []})
        self.assertEqual(len(coord.faces_seen()["7"]["trail"]),
                         const.FACE_TRAIL_MAX)

    def test_someone_seen_at_one_camera_has_a_one_stop_trail(self):
        coord = self._two_cameras({
            0: [{"startTime": 100, "event_info": [{"face_id": 7}]}], 1: []})
        face = coord.faces_seen()["7"]
        self.assertEqual(face["cameras"], ["Front"])
        self.assertEqual(len(face["trail"]), 1)

    def test_no_sighting_means_no_location_rather_than_a_stale_one(self):
        """Reporting the last known camera forever would read as "they are at
        the front door" long after they left."""
        SENSOR_SRC = (COMPONENT / "sensor.py").read_text()
        body = SENSOR_SRC.split("class H500FaceLocationSensor", 1)[1]
        self.assertIn('return self._face.get("last_camera")', body)

    def test_the_location_sensor_is_added_with_the_time_one(self):
        self.assertIn("H500FaceLocationSensor(coordinator, entry, face_id)",
                      SENSOR)


class SeenRecently(unittest.TestCase):
    """Presence, framed honestly: a camera watches a doorstep, not a house."""

    def test_it_is_not_a_device_tracker(self):
        """Off means "not seen", which is not "away". Modelling it as presence
        would invite occupancy automations built on a guess.

        Checked against the class declaration rather than the file: the
        docstring says "deliberately not a device_tracker", and a whole-file
        search matches that sentence.
        """
        BS = (COMPONENT / "binary_sensor.py").read_text()
        declaration = BS.split("class H500FaceSeenRecently", 1)[1].split(":", 1)[0]
        self.assertIn("BinarySensorEntity", declaration)
        self.assertNotIn("Tracker", declaration)
        platforms = (COMPONENT / "__init__.py").read_text()
        self.assertNotIn("Platform.DEVICE_TRACKER", platforms)

    def test_its_name_says_what_it_means(self):
        BS = (COMPONENT / "binary_sensor.py").read_text()
        self.assertIn('return f"{who} seen recently"', BS)

    def test_never_seen_is_off_not_unknown(self):
        BS = (COMPONENT / "binary_sensor.py").read_text()
        body = BS.split("class H500FaceSeenRecently", 1)[1]
        self.assertIn("if last is None:\n            return False", body)

    def test_the_window_is_minutes(self):
        const_mod = importlib.import_module("tapo_h500.const")
        self.assertLessEqual(const_mod.FACE_PRESENCE_WINDOW, 3600)
        self.assertGreaterEqual(const_mod.FACE_PRESENCE_WINDOW, 60)


class NamingDoesNotReload(unittest.TestCase):
    """The reported crash: the card asked for a name, the integration reloaded
    underneath it, and the next request found no coordinator."""

    def test_only_connection_options_trigger_a_reload(self):
        CONST = (COMPONENT / "const.py").read_text()
        block = CONST.split("RELOAD_ON_CHANGE = (", 1)[1].split(")", 1)[0]
        self.assertIn("CONF_POLL_INTERVAL", block)
        # Face names must NOT be in it, or naming reloads again.
        self.assertNotIn("CONF_FACE_NAMES", block)
        # Nothing else belongs: every other option is read live at use time,
        # so reloading for one is a fresh login bought for nothing -- and
        # repeated logins are the one thing that wedges this hub.
        names = [name for name in block.replace(",", " ").split()
                 if name.startswith("CONF_")]
        self.assertEqual(names, ["CONF_POLL_INTERVAL"], block)

    def test_a_name_only_change_skips_the_reload(self):
        body = INIT.split("async def _async_options_changed", 1)[1][:1400]
        self.assertIn("return", body)
        self.assertIn("async_reload", body)
        # The early return must come before the reload, or it never skips.
        self.assertLess(body.index("        return"), body.index("async_reload"))

    def test_it_still_reloads_when_the_connection_changes(self):
        body = INIT.split("async def _async_options_changed", 1)[1][:1400]
        self.assertIn("await hass.config_entries.async_reload(entry.entry_id)", body)

    def test_a_missing_coordinator_falls_back_to_reloading(self):
        """Mid-teardown there is nothing to compare against; reloading is the
        safe answer, not skipping."""
        body = INIT.split("async def _async_options_changed", 1)[1][:1400]
        self.assertIn("coordinator is not None", body)

    def test_new_faces_appear_without_a_reload(self):
        # Scoped to setup: searching the whole file matches the IMPORT of
        # async_dispatcher_connect and passes even when nothing subscribes.
        body = SENSOR.split("async def async_setup_entry", 1)[1] \
                     .split("class H500HubSensor", 1)[0]
        self.assertIn("def _sync_faces", body)
        self.assertIn("async_dispatcher_connect(", body)
        self.assertIn("SIGNAL_FACES_CHANGED", body)

    def test_a_person_is_not_added_twice(self):
        """Guarded on the whole group of ids rather than on one id.

        A second cluster the hub invented for somebody already named joins
        them; keying on the id alone would add a second entity with the same
        name, which is the duplication the merging exists to remove.
        """
        body = SENSOR.split("def _sync_faces", 1)[1].split("_sync_faces()", 1)[0]
        self.assertIn("added.intersection(ids)", body)

    def test_renaming_takes_effect_without_a_reload(self):
        """A name captured at construction would show the old one until
        restart, and avoiding that restart is the point."""
        self.assertIn("def name(self) -> str:", SENSOR)
        self.assertIn("self.coordinator.face_names.get(self.face_id)", SENSOR)


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

    def test_a_sensor_exists_per_named_person(self):
        # The name is no longer passed in: it is read live off the coordinator
        # so a rename does not need a reload. Grouped by name rather than by
        # face id, because the hub clusters one person more than once.
        self.assertIn("H500FaceSensor(coordinator, entry, face_id)", SENSOR)
        self.assertIn("coordinator.named_people.values()", SENSOR)


if __name__ == "__main__":
    unittest.main()


class FacesWithFaces(unittest.TestCase):
    """A named person's entities carry their photograph.

    The hub has a picture of everyone it recognises; the per-person sensors
    showed a generic icon. entity_picture now points at the preview endpoint
    for their newest sighting -- which generates the frame from the hub if
    no download ever wrote it, and is cached on disk after the first look.
    """

    SENSOR = (COMPONENT / "sensor.py").read_text()
    BODY = SENSOR.split("class H500FaceSensor", 1)[1].split("\nclass ", 1)[0]

    def test_the_picture_is_their_newest_sighting(self):
        self.assertIn("def entity_picture", self.BODY)
        self.assertIn("preview_url", self.BODY)
        self.assertIn("last_seen", self.BODY.split("def entity_picture", 1)[1])
        self.assertIn("camera_index",
                      self.BODY.split("def entity_picture", 1)[1])

    def test_the_url_is_stable_between_sightings(self):
        """A fresh signature per poll would make the frontend refetch the
        same photograph every two seconds. The URL is cached per sighting
        and only re-signed as its signature nears expiry."""
        body = self.BODY.split("def entity_picture", 1)[1]
        self.assertIn("PICTURE_RESIGN_SECONDS", body)
        self.assertIn("_picture_for", body)

    def test_a_person_never_seen_shows_no_broken_image(self):
        body = self.BODY.split("def entity_picture", 1)[1]
        self.assertIn("return None", body)

    def test_resigning_happens_well_inside_the_signatures_life(self):
        import importlib
        const_mod = importlib.import_module("tapo_h500.const")
        media_src = (COMPONENT / "media.py").read_text()
        # URL_LIFETIME is 12 hours; re-sign at half that.
        self.assertLessEqual(const_mod.PICTURE_RESIGN_SECONDS, 6 * 3600)
        self.assertIn("URL_LIFETIME = timedelta(hours=12)", media_src)
