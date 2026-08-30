"""The last uncovered corners, driven: calendar, event entity, the sensor
tables' tail, and the setup registrars.

Nothing here is speculative coverage. Each block holds a decision that was
made for a reason -- the calendar clamps to what the hub can actually answer,
the event entity computes "notable" from the configured night, and setup
composes the entity list the dashboard depends on.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

calendar_mod = importlib.import_module("tapo_h500.calendar")
event_mod = importlib.import_module("tapo_h500.event")
sensor_mod = importlib.import_module("tapo_h500.sensor")
number_mod = importlib.import_module("tapo_h500.number")
image_mod = importlib.import_module("tapo_h500.image")
preview_mod = importlib.import_module("tapo_h500.preview")
dt_util = sys.modules["homeassistant.util.dt"]

NOW = int(dt_util.utcnow().timestamp())
CAMERA = {"device_id": "cam0", "alias": "Front"}


def clip(start, end=None, mask=1 << 1, face=None):
    made = {"startTime": start, "events_1": mask}
    if end is not None:
        made["endTime"] = end
    if face is not None:
        made["event_info"] = [{"face_id": face}]
    return made


def run(coro):
    return asyncio.run(coro)


def _local_hour_ts(hour):
    """A timestamp whose LOCAL hour is `hour`, near the frozen now."""
    local_now = dt_util.as_local(dt_util.utc_from_timestamp(NOW))
    return NOW + (hour - local_now.hour) * 3600


class TheCalendar(unittest.TestCase):
    def _calendar(self, detections=None, recent=None):
        coord, client = harness._build()
        coord.clips_for = lambda index: []
        calls = {"windows": []}

        def get_detections(camera, begin, until):
            calls["windows"].append((begin, until))
            if isinstance(detections, Exception):
                raise detections
            return detections

        def get_recent(camera, begin, until):
            if isinstance(recent, Exception):
                raise recent
            return recent or []

        client.detections = get_detections
        client.recent = get_recent
        coord.client = client
        entity = calendar_mod.H500Calendar(coord, 0, CAMERA)
        return entity, coord, calls

    def _events(self, entity, days=3):
        start = dt_util.utc_from_timestamp(NOW - days * 86400)
        end = dt_util.utc_from_timestamp(NOW)
        return run(entity.async_get_events(harness._Hass(), start, end))

    def test_a_days_detections_become_clickable_entries_in_order(self):
        entity, _, _ = self._calendar(detections=[
            clip(NOW - 60, NOW - 45), clip(NOW - 7200, NOW - 7185)])
        found = self._events(entity)
        self.assertEqual(len(found), 2)
        self.assertLess(found[0].start, found[1].start)

    def test_a_year_view_is_clamped_to_what_the_hub_can_answer(self):
        """searchDetectionList caps at 1000 records; a huge window silently
        truncates. Better a bounded month that says so than a month of a
        year presented as the year."""
        entity, _, calls = self._calendar(detections=[])
        self._events(entity, days=365)
        begin, until = calls["windows"][0]
        self.assertLessEqual(until - begin, 31 * 86400)

    def test_a_hub_with_no_detection_log_falls_back_to_the_clip_index(self):
        entity, _, _ = self._calendar(detections=None,
                                      recent=[clip(NOW - 60, NOW - 45)])
        self.assertEqual(len(self._events(entity)), 1)

    def test_a_hub_answering_nothing_shows_an_empty_view_not_an_error(self):
        entity, _, _ = self._calendar(detections=OSError("wedged"))
        self.assertEqual(self._events(entity), [])
        entity, _, _ = self._calendar(detections=None,
                                      recent=OSError("wedged"))
        self.assertEqual(self._events(entity), [])

    def test_the_current_entry_is_the_most_recent_one(self):
        """A doorbell has no future, and a recording is indexed only once
        finished -- the newest is the only useful answer."""
        entity, coord, _ = self._calendar()
        coord.clips_for = lambda index: [clip(NOW - 7200, NOW - 7185),
                                         clip(NOW - 60, NOW - 45)]
        self.assertEqual(int(entity.event.start.timestamp()), NOW - 60)
        coord.clips_for = lambda index: []
        self.assertIsNone(entity.event)

    def test_an_endless_detection_still_draws_wide_enough_to_click(self):
        entity, _, _ = self._calendar(detections=[clip(NOW - 60)])
        entry = self._events(entity)[0]
        self.assertEqual(int((entry.end - entry.start).total_seconds()),
                         calendar_mod.ASSUMED_SECONDS)

    def test_a_clock_that_moved_cannot_draw_a_negative_entry(self):
        entity, _, _ = self._calendar(
            detections=[clip(NOW - 60, NOW - 90)])
        entry = self._events(entity)[0]
        self.assertGreater(entry.end, entry.start)

    def test_the_row_reads_as_what_and_who(self):
        coord, _ = harness._build()
        coord.entry.options = {**coord.entry.options,
                               "face_names": {"7": "Alice"}}
        entity = calendar_mod.H500Calendar(coord, 0, CAMERA)
        entry = entity._entry(clip(NOW - 60, NOW - 45,
                                   mask=(1 << 1) | (1 << 5), face=7))
        self.assertEqual(entry.summary, "Motion + person — Alice")
        self.assertEqual(entry.location, "Front")
        self.assertIn("7", entry.description)

    def test_no_faces_means_no_description_rather_than_an_empty_label(self):
        coord, _ = harness._build()
        entity = calendar_mod.H500Calendar(coord, 0, CAMERA)
        self.assertIsNone(entity._entry(clip(NOW - 60, NOW - 45)).description)


class TheEventEntity(unittest.TestCase):
    def _entity(self, names=None, night=(22, 6)):
        coord, _ = harness._build()
        options = dict(coord.entry.options)
        if names:
            options["face_names"] = names
        options["night_start"], options["night_end"] = night
        coord.entry.options = options
        return event_mod.H500ActivityEvent(coord, 0, CAMERA)

    def test_two_people_arriving_together_always_read_the_same_way_round(self):
        entity = self._entity(names={"7": "Zoe", "8": "Alice"})
        detection = {"event_info": [{"face_id": 7}, {"face_id": 8}]}
        self.assertEqual(entity._known_faces(detection), ["Alice", "Zoe"])

    def test_an_unfamiliar_face_at_night_is_notable(self):
        entity = self._entity()
        at_night = _local_hour_ts(23)
        detection = {"events_1": 1 << 21}
        self.assertTrue(entity._notable(detection, at_night))

    def test_the_same_face_at_midday_is_not(self):
        entity = self._entity()
        midday = _local_hour_ts(13)
        self.assertFalse(entity._notable({"events_1": 1 << 21}, midday))

    def test_no_timestamp_is_never_notable(self):
        self.assertFalse(self._entity()._notable({"events_1": 1 << 21}, None))

    def test_the_frame_url_is_pinned_to_the_events_own_moment(self):
        entity = self._entity()
        entity.hass = harness._Hass()
        # clip_path derives the path from the media root; without one the
        # frame is honestly unbuildable and the guard answers None.
        entity.hass.config = type("C", (), {
            "media_dirs": {"local": "/media"}})()
        url = entity._own_frame(NOW - 60)
        self.assertIn("authSig", url)

    def test_a_frame_that_cannot_be_signed_is_no_frame_not_a_crash(self):
        entity = self._entity()
        entity.hass = harness._Hass()
        original = event_mod.signed_url
        event_mod.signed_url = lambda hass, path: (_ for _ in ()).throw(
            RuntimeError("no signer"))
        try:
            self.assertIsNone(entity._own_frame(NOW - 60))
        finally:
            event_mod.signed_url = original
        self.assertIsNone(entity._own_frame(None))


class TheHubTableTail(unittest.TestCase):
    def _value(self, key, readings):
        description = next(d for d in sensor_mod.HUB_SENSORS if d.key == key)
        return description.value(readings)

    def _attrs(self, key, readings):
        description = next(d for d in sensor_mod.HUB_SENSORS if d.key == key)
        return description.attributes(readings)

    def test_the_passthrough_readings_pass_through(self):
        for key, reading in (("storage_free", "storage_free_gb"),
                             ("storage_total", "storage_total_gb"),
                             ("storage_used", "storage_used_percent"),
                             ("storage_status", "storage_status"),
                             ("siren_time_left", "siren_time_left"),
                             ("firmware_state", "firmware_state"),
                             ("clock_offset", "clock_offset"),
                             ("timezone", "timezone"),
                             ("ip_address", "ip_address")):
            with self.subTest(key):
                self.assertEqual(self._value(key, {reading: "x"}), "x")

    def test_the_upgrade_time_says_whether_the_schedule_is_running(self):
        """The time is stored whether or not updates are on; alone it reads
        as a schedule that is running when it may not be."""
        attrs = self._attrs("auto_upgrade_time",
                            {"auto_upgrade": False,
                             "auto_upgrade_config": {"random_range": 120}})
        self.assertEqual(attrs, {"enabled": False, "random_range": 120})

    def test_the_reboot_schedule_carries_its_raw_day(self):
        """The hub's own numbering, passed through: the only value ever seen
        was 0 on a schedule that was off, which says nothing of what 0
        means."""
        attrs = self._attrs("scheduled_reboot",
                            {"scheduled_reboot_enabled": True,
                             "scheduled_reboot_day": 0})
        self.assertEqual(attrs, {"enabled": True, "day": 0})


class TheRegistrars(unittest.TestCase):
    def _setup(self, module, cameras=2):
        coord, _ = harness._build()
        coord.cameras = [{"device_id": f"cam{n}", "alias": f"C{n}"}
                         for n in range(cameras)]
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
        added = []
        entry = harness._Entry(20)
        entry.async_on_unload = lambda unsub: None
        run(module.async_setup_entry(hass, entry, added.extend))
        return added, coord

    def test_the_sensor_platform_composes_by_its_tables(self):
        added, coord = self._setup(sensor_mod)
        expected = (len(sensor_mod.HUB_SENSORS)
                    + 2 * len(sensor_mod.CAMERA_SENSORS)
                    + 2 * 2      # visits + activity per camera
                    + 4)         # forecast, wedge clock, sessions, household
        self.assertEqual(len(added), expected)

    def test_nobody_named_at_setup_means_no_pair_yet(self):
        added, coord = self._setup(sensor_mod)
        coord.entry.options = {**coord.entry.options,
                               "face_names": {"7": "Alice"}}
        pairs = [e for e in added
                 if isinstance(e, (sensor_mod.H500FaceSensor,
                                   sensor_mod.H500FaceLocationSensor))]
        self.assertEqual(pairs, [], "nobody named at setup, no pair yet")

    def test_a_named_person_at_setup_gets_both_halves(self):
        coord, _ = harness._build()
        coord.cameras = [CAMERA]
        coord.entry.options = {**coord.entry.options,
                               "face_names": {"7": "Alice", "9": "Alice"}}
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
        added = []
        entry = harness._Entry(20)
        entry.async_on_unload = lambda unsub: None
        run(sensor_mod.async_setup_entry(hass, entry, added.extend))
        when = [e for e in added if isinstance(e, sensor_mod.H500FaceSensor)]
        where = [e for e in added
                 if isinstance(e, sensor_mod.H500FaceLocationSensor)]
        self.assertEqual((len(when), len(where)), (1, 1),
                         "one pair per PERSON, not per cluster")

    def test_the_small_platforms_register_their_own(self):
        numbers, _ = self._setup(number_mod)
        self.assertEqual(len(numbers), 2)
        images, _ = self._setup(image_mod)
        self.assertEqual(len(images), 4, "event image and sheet per camera")


class ThePreviewUrl(unittest.TestCase):
    def test_it_addresses_the_clip_and_is_signed(self):
        url = preview_mod.preview_url(harness._Hass(), "test", 0, NOW)
        self.assertIn(f"/api/tapo_h500/preview/test/0/{NOW}", url)
        self.assertIn("authSig", url)


if __name__ == "__main__":
    unittest.main()


class TheRemainingRegistrars(unittest.TestCase):
    def _setup(self, module, cameras=2):
        coord, _ = harness._build()
        coord.cameras = [{"device_id": f"cam{n}", "alias": f"C{n}"}
                         for n in range(cameras)]
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
        added = []
        entry = harness._Entry(20)
        entry.async_on_unload = lambda unsub: None
        run(module.async_setup_entry(hass, entry, added.extend))
        return added

    def test_event_camera_and_update_register_theirs(self):
        camera_mod = importlib.import_module("tapo_h500.camera")
        update_mod = importlib.import_module("tapo_h500.update")
        self.assertEqual(len(self._setup(event_mod)), 2, "one per camera")
        self.assertEqual(len(self._setup(camera_mod)), 2)
        self.assertEqual(len(self._setup(update_mod)), 1, "one per hub")

    def test_the_event_entity_subscribes_to_its_own_camera(self):
        entity = self._setup(event_mod)[1]
        seen = []
        original = event_mod.async_dispatcher_connect

        def record(hass, signal, target):
            seen.append(signal)
            return lambda: None

        event_mod.async_dispatcher_connect = record
        try:
            entity.hass = harness._Hass()
            entity.async_on_remove = lambda unsub: None
            run(entity.async_added_to_hass())
        finally:
            event_mod.async_dispatcher_connect = original
        self.assertEqual(len(seen), 1)
        self.assertIn("_1", seen[0], "camera 1's signal, not camera 0's")


class TriggerListingEdges(unittest.TestCase):
    def _triggers(self, entities=(), device=None):
        import types as types_mod
        trigger_mod = importlib.import_module("tapo_h500.device_trigger")
        er = sys.modules["homeassistant.helpers.entity_registry"]
        dr = sys.modules["homeassistant.helpers.device_registry"]
        hass = harness._Hass()
        coord, _ = harness._build()
        hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
        patches = [
            (er, "async_get", lambda h: object()),
            (er, "async_entries_for_device",
             lambda reg, device_id: list(entities)),
            (dr, "async_get",
             lambda h: types_mod.SimpleNamespace(
                 async_get=lambda device_id: device)),
        ]
        originals = [(m, n, getattr(m, n, None)) for m, n, _ in patches]
        for module, name, value in patches:
            setattr(module, name, value)
        try:
            return run(trigger_mod.async_get_triggers(hass, "device-1"))
        finally:
            for module, name, value in originals:
                if value is not None:
                    setattr(module, name, value)

    def test_a_device_the_registry_has_forgotten_offers_nothing(self):
        """dr.async_get answering None must not crash the automation
        editor."""
        self.assertEqual(self._triggers(device=None), [])

    def test_unrelated_entity_domains_contribute_nothing(self):
        import types as types_mod
        rows = [types_mod.SimpleNamespace(domain="sensor",
                                          unique_id="cam0_visits_24h",
                                          id="reg-v"),
                types_mod.SimpleNamespace(domain="switch",
                                          unique_id="hub_led", id="reg-l")]
        self.assertEqual(self._triggers(rows), [])


class TheLastRegistrars(unittest.TestCase):
    """Every platform's setup, so a renamed constant cannot silently stop a
    whole class of entity appearing."""

    def _added(self, module_name, cameras=2):
        module = importlib.import_module(f"tapo_h500.{module_name}")
        coord, _ = harness._build()
        coord.cameras = [{"device_id": f"cam{n}", "alias": f"C{n}"}
                         for n in range(cameras)]
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
        entry = harness._Entry(20)
        entry.async_on_unload = lambda unsub: None
        added = []
        run(module.async_setup_entry(hass, entry, added.extend))
        return added

    def test_the_button_and_the_calendar_register_theirs(self):
        self.assertEqual(len(self._added("button")), 1, "one per hub")
        self.assertEqual(len(self._added("calendar")), 2, "one per camera")

    def test_a_hub_with_no_cameras_still_gets_its_button(self):
        """A hub whose cameras have not been read yet must not leave the
        entry with no entities at all."""
        self.assertEqual(len(self._added("button", cameras=0)), 1)
        self.assertEqual(self._added("calendar", cameras=0), [])


class TheHubSensorProperties(unittest.TestCase):
    def _sensor(self, attributes=None, value=lambda readings: 42):
        sensor_mod = importlib.import_module("tapo_h500.sensor")
        coord, _ = harness._build()
        coord.readings = {"storage_used": 61}
        description = sensor_mod.HubSensor(
            key="probe", value=value, attributes=attributes)
        return sensor_mod.H500HubSensor(coord, coord.entry, description)

    def test_a_reading_with_nothing_to_add_has_no_attributes_at_all(self):
        """Not {}. An empty dictionary is a set of attributes, and Home
        Assistant records it on every state change of every hub sensor."""
        self.assertIsNone(self._sensor().extra_state_attributes)

    def test_a_reading_that_has_something_to_add_says_it(self):
        sensor = self._sensor(
            attributes=lambda readings: {"used": readings["storage_used"]})
        self.assertEqual(sensor.extra_state_attributes, {"used": 61})

    def test_the_value_is_read_from_the_coordinators_readings(self):
        sensor = self._sensor(value=lambda readings: readings["storage_used"])
        self.assertEqual(sensor.native_value, 61)


class TheForecastSensor(unittest.TestCase):
    def _forecast(self, trend):
        sensor_mod = importlib.import_module("tapo_h500.sensor")
        coord, _ = harness._build()
        coord.storage_trend = list(trend)
        coord.days_until_full = lambda: None if len(trend) < 2 else 9
        return sensor_mod.H500StorageForecast(coord, coord.entry)

    def test_too_little_history_reads_as_measuring_not_as_never_filling(self):
        """The attributes are how somebody tells those two apart, and they
        are the difference between "fine" and "no data yet"."""
        forecast = self._forecast([(0, 10.0)])
        self.assertIsNone(forecast.native_value)
        attributes = forecast.extra_state_attributes
        self.assertIsNone(attributes["percent_per_hour"])
        self.assertEqual(attributes["samples"], 1)

    def test_a_measured_rate_is_reported_with_its_sample_count(self):
        forecast = self._forecast([(0, 10.0), (3600, 11.0), (7200, 12.0)])
        attributes = forecast.extra_state_attributes
        self.assertIsNotNone(attributes["percent_per_hour"])
        self.assertEqual(attributes["samples"], 3)
        self.assertEqual(forecast.native_value, 9)


class TheFaceSensorsName(unittest.TestCase):
    def _named(self, face_id, names):
        sensor_mod = importlib.import_module("tapo_h500.sensor")
        coord, _ = harness._build()
        # face_names is derived from the entry's options, and normalising
        # the keys is part of what it does -- so set it where it reads from.
        coord.entry.options = {**coord.entry.options, "face_names": dict(names)}
        return sensor_mod.H500FaceLocationSensor(
            coord, coord.entry, face_id).name

    def test_a_named_face_is_called_by_its_name(self):
        self.assertEqual(self._named("7", {"7": "Sam"}), "Sam last seen at")

    def test_a_name_stored_under_a_numeric_id_is_still_found(self):
        """The hub reports ids as numbers and the services API hands them
        over as either; a map answering to 7 but not "7" reads as empty."""
        self.assertEqual(self._named("7", {7: "Sam"}), "Sam last seen at")

    def test_an_unnamed_face_still_reads_as_something(self):
        """Before anybody names it, the entity is still in the list and has
        to say which face it is."""
        self.assertEqual(self._named("7", {}), "Face 7 last seen at")
