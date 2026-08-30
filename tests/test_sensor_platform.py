"""The sensor tables and classes, evaluated rather than read.

sensor.py sat at 66% -- the hub readings table, the per-camera lambdas, the
visits and activity sensors and the per-person pair had never been driven.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

sensor = importlib.import_module("tapo_h500.sensor")
const = importlib.import_module("tapo_h500.const")
dt_util = sys.modules["homeassistant.util.dt"]

NOW = int(dt_util.utcnow().timestamp())
CAMERA = {"device_id": "cam0", "alias": "Front", "device_model": "TD21",
          "ai_enhance": 55, "network_mode": "wifi"}


def clip(start, mask=1 << 1, face=None):
    made = {"startTime": start, "endTime": start + 15, "events_1": mask}
    if face is not None:
        made["event_info"] = [{"face_id": face}]
    return made


def hub_value(key, readings):
    description = next(d for d in sensor.HUB_SENSORS if d.key == key)
    return description.value(readings)


def camera_value(key, coord, camera=CAMERA):
    description = next(d for d in sensor.CAMERA_SENSORS if d.key == key)
    return description.value(coord, 0, camera)


class TheHubTable(unittest.TestCase):
    def test_health_names_the_worst_thing_first(self):
        self.assertEqual(hub_value("hub_health", {}), "unreachable")
        self.assertEqual(hub_value("hub_health",
                                   {"storage_used_percent": 99.5}),
                         "storage full")
        self.assertEqual(hub_value("hub_health",
                                   {"storage_used_percent": 40,
                                    "storage_healthy": False}),
                         "storage failing")
        self.assertEqual(hub_value("hub_health",
                                   {"storage_used_percent": 40,
                                    "storage_healthy": True,
                                    "clock_offset": 300}),
                         "clock drifted")
        self.assertEqual(hub_value("hub_health",
                                   {"storage_used_percent": 40,
                                    "storage_healthy": True,
                                    "clock_offset": 5}),
                         "ok")

    def test_overlapping_faults_report_the_worst(self):
        """A hub can be full AND drifted; losing footage outranks a wrong
        clock, and the order of the checks is what says so."""
        self.assertEqual(hub_value("hub_health",
                                   {"storage_used_percent": 99.5,
                                    "storage_healthy": False,
                                    "clock_offset": 300}),
                         "storage full")
        self.assertEqual(hub_value("hub_health",
                                   {"storage_used_percent": 50,
                                    "storage_healthy": False,
                                    "clock_offset": 300}),
                         "storage failing")

    def test_custom_sounds_carry_their_names_as_attributes(self):
        description = next(d for d in sensor.HUB_SENSORS
                           if d.key == "custom_sounds")
        self.assertEqual(description.attributes(
            {"custom_sound_names": ["Bark", "Chime"]}),
            {"names": ["Bark", "Chime"]})


class TheCameraTable(unittest.TestCase):
    def _coord(self, clips):
        coord, _ = harness._build()
        coord.clips_for = lambda index: list(clips)
        return coord

    def test_last_activity_is_a_datetime_or_none(self):
        coord = self._coord([clip(NOW - 300)])
        self.assertEqual(int(camera_value("last_activity",
                                          coord).timestamp()), NOW - 300)
        self.assertIsNone(camera_value("last_activity", self._coord([])))

    def test_the_two_recording_counts_ask_different_questions(self):
        coord = self._coord([clip(NOW - 60), clip(NOW - 7200)])
        self.assertEqual(camera_value("recordings_1h", coord), 1)
        self.assertEqual(camera_value("recordings_24h", coord), 2)

    def test_the_static_camera_fields_pass_through(self):
        coord = self._coord([])
        self.assertEqual(camera_value("ai_enhance", coord), 55)
        self.assertEqual(camera_value("network_mode", coord), "wifi")
        self.assertEqual(camera_value("model", coord), "TD21")

    def test_people_seen_counts_distinct_faces_named_or_not(self):
        coord = self._coord([clip(NOW - 60, face=7), clip(NOW - 50, face=7),
                             clip(NOW - 40, face=8)])
        self.assertEqual(camera_value("people_seen", coord), 2)


class Visits(unittest.TestCase):
    def _visits(self, clips):
        coord, _ = harness._build()
        coord.clips_for = lambda index: list(clips)
        return sensor.H500Visits(coord, 0, CAMERA)

    def test_sixteen_clips_of_one_wait_are_one_visit(self):
        """The hub reports moments, not presence. "48 recordings" and
        "3 visits" can be the same day."""
        wait = [clip(NOW - 240 + n * 15) for n in range(16)]
        entity = self._visits(wait)
        self.assertEqual(entity.native_value, 1)
        self.assertGreaterEqual(
            entity.extra_state_attributes["longest_seconds"], 200)

    def test_two_far_apart_callers_are_two(self):
        entity = self._visits([clip(NOW - 7200), clip(NOW - 60)])
        self.assertEqual(entity.native_value, 2)

    def test_the_attributes_carry_the_shape_of_the_day(self):
        entity = self._visits([clip(NOW - 60)])
        attributes = entity.extra_state_attributes
        self.assertEqual(len(attributes["hourly"]), 24)
        self.assertEqual(attributes["gap_seconds"], const.LOITER_GAP)


class ActivityLevel(unittest.TestCase):
    def _level(self, clips):
        coord, _ = harness._build()
        coord.clips_for = lambda index: list(clips)
        coord.sensitivity = lambda index: (2.0, 3)
        return sensor.H500ActivityLevel(coord, 0, CAMERA)

    def test_the_scale_reads_off_one_camera_day(self):
        self.assertEqual(self._level([]).native_value, "quiet")
        self.assertEqual(
            self._level([clip(NOW - 300)]).native_value, "active")
        burst = [clip(NOW - 3600 * n) for n in range(3, 20)] + \
                [clip(NOW - 60 * n) for n in range(1, 10)]
        self.assertEqual(self._level(burst).native_value, "unusual")

    def test_the_two_thresholds_ride_along_for_the_why(self):
        entity = self._level([clip(NOW - 300)])
        attributes = entity.extra_state_attributes
        self.assertEqual(attributes["events_last_hour"], 1)
        self.assertEqual(attributes["unusual_at"],
                         attributes["busy_at"] * 2)


class Household(unittest.TestCase):
    def _household(self, people):
        coord, _ = harness._build()
        coord.household = lambda window: dict(people)
        coord.entry.options = {**coord.entry.options,
                               "face_names": {"7": "Alice", "8": "Bob"}}
        return sensor.H500Household(coord, harness._Entry(20))

    def test_the_count_is_who_was_seen_and_the_lists_say_who(self):
        entity = self._household({"seen_recently": ["Alice"],
                                  "not_seen": ["Bob"]})
        self.assertEqual(entity.native_value, 1)
        attributes = entity.extra_state_attributes
        self.assertEqual(attributes["not_seen"], ["Bob"])
        self.assertEqual(attributes["named"], ["Alice", "Bob"],
                         "an empty house and nobody-named-yet differ")


class TheNamedPersonPair(unittest.TestCase):
    def _coord(self, person, names=None):
        coord, _ = harness._build()
        coord.person_for = lambda face_id: dict(person)
        coord.entry.options = {**coord.entry.options,
                               "face_names": names or {"7": "Alice"}}
        return coord

    def test_the_timestamp_sensor_reads_the_merged_person(self):
        coord = self._coord({"last_seen": NOW - 120, "sightings": 4,
                             "ids": ["7", "9"], "cameras": ["Front"],
                             "first_seen": NOW - 4000})
        entity = sensor.H500FaceSensor(coord, harness._Entry(20), "7")
        self.assertEqual(entity.name, "Alice")
        self.assertEqual(int(entity.native_value.timestamp()), NOW - 120)
        self.assertEqual(entity.extra_state_attributes["face_ids"],
                         ["7", "9"], "one id alone misses half the sightings")

    def test_renaming_takes_effect_without_a_reload(self):
        coord = self._coord({"last_seen": NOW})
        entity = sensor.H500FaceSensor(coord, harness._Entry(20), "7")
        self.assertEqual(entity.name, "Alice")
        coord.entry.options = {**coord.entry.options,
                               "face_names": {"7": "Alicia"}}
        self.assertEqual(entity.name, "Alicia")

    def test_never_seen_is_none_not_epoch(self):
        coord = self._coord({})
        entity = sensor.H500FaceSensor(coord, harness._Entry(20), "7")
        self.assertIsNone(entity.native_value)

    def test_the_location_reports_where_last_seen_or_nothing(self):
        coord = self._coord({"last_camera": "Front",
                             "trail": [{"camera": "Front", "at": NOW - 60}],
                             "direction": "approaching"})
        entity = sensor.H500FaceLocationSensor(coord, harness._Entry(20), "7")
        self.assertEqual(entity.name, "Alice last seen at")
        self.assertEqual(entity.native_value, "Front")
        trail = entity.extra_state_attributes["trail"]
        self.assertTrue(trail[0]["at"].startswith("20"))

    def test_outside_the_window_there_is_genuinely_no_answer(self):
        """Inventing one reads as "they are at the front door" long after
        they left."""
        coord = self._coord({})
        entity = sensor.H500FaceLocationSensor(coord, harness._Entry(20), "7")
        self.assertIsNone(entity.native_value)


class ThePersonPicture(unittest.TestCase):
    def _entity(self, person):
        coord, _ = harness._build()
        coord.person_for = lambda face_id: dict(person)
        entity = sensor.H500FaceSensor(coord, harness._Entry(20), "7")
        entity.hass = harness._Hass()
        self.signed = []
        self.addCleanup(setattr, sensor, "preview_url", sensor.preview_url)
        sensor.preview_url = (
            lambda hass, entry_id, index, seen:
            self.signed.append(seen) or f"/preview/{seen}")
        return entity

    def test_the_photo_is_their_newest_sighting(self):
        entity = self._entity({"last_seen": NOW - 60, "camera_index": 0})
        self.assertEqual(entity.entity_picture, f"/preview/{NOW - 60}")

    def test_the_signature_is_cached_per_sighting(self):
        """A fresh signature per poll would make the frontend refetch the
        same photograph every two seconds."""
        entity = self._entity({"last_seen": NOW - 60, "camera_index": 0})
        entity.entity_picture
        entity.entity_picture
        self.assertEqual(len(self.signed), 1)

    def test_nobody_placed_means_no_picture(self):
        entity = self._entity({})
        self.assertIsNone(entity.entity_picture)


if __name__ == "__main__":
    unittest.main()
