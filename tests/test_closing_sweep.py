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
import ha_stubs  # noqa: E402

component = ha_stubs.real_module("init")

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


class ThingsAPollSurvives(unittest.TestCase):
    """A poll is how every entity in this integration learns anything, so
    the bonuses hung off it must not be able to take it down."""

    def _poll_with(self, attribute, error=RuntimeError("no")):
        coord, client = harness._build()

        def boom(*args, **kwargs):
            raise error

        setattr(coord, attribute, boom)
        return run(coord._async_update_data())

    def test_arrival_tracking_failing_does_not_fail_the_poll(self):
        self.assertIn("clips", self._poll_with("_note_arrivals"))

    def test_visit_tracking_failing_does_not_fail_the_poll(self):
        self.assertIn("clips", self._poll_with("_note_visits"))

    def test_the_clips_still_arrive_when_both_fail(self):
        """Neither is worth a poll: the detections are the reason for it."""
        coord, client = harness._build()
        coord._note_arrivals = coord._note_visits = self._explode
        self.assertIn("clips", run(coord._async_update_data()))

    @staticmethod
    def _explode(*args, **kwargs):
        raise RuntimeError("no")


class CameraRanksFromOptions(unittest.TestCase):
    """The layout is typed in by hand, so it arrives however YAML felt."""

    def _ranks(self, stored):
        coord, _ = harness._build()
        coord.entry.options = {**coord.entry.options, "camera_order": stored}
        return coord.camera_ranks

    def test_numbers_written_as_text_are_still_numbers(self):
        self.assertEqual(self._ranks({"Front": "1"}), {"Front": 1})

    def test_an_entry_that_is_not_a_number_is_dropped_not_fatal(self):
        """One bad row must not cost the whole layout, and the trail reads
        this on every detection."""
        self.assertEqual(self._ranks({"Front": 1, "Side": "near", "Back": 3}),
                         {"Front": 1, "Back": 3})

    def test_nothing_configured_is_an_empty_layout(self):
        self.assertEqual(self._ranks({}), {})


class TheDownloadsTwoSkips(unittest.TestCase):
    def _attempt(self, clip, existing=None):
        """Run one automatic download with the disk stubbed out, and report
        whether the hub was actually asked for the recording."""
        coord, _ = harness._build()
        asked = []
        module = sys.modules["tapo_h500.coordinator"]

        async def download(*args, **kwargs):
            asked.append(args)
            return {"path": "/x.ts", "bytes": 4096}

        async def verified(*args, **kwargs):
            return True

        async def pruned(*args, **kwargs):
            return []

        for name, value in (("existing_clip",
                             lambda hass, camera, start: existing),
                            ("async_download_clip", download),
                            ("async_verify", verified),
                            ("async_prune", pruned)):
            self.addCleanup(setattr, module, name, getattr(module, name, None))
            setattr(module, name, value)
        run(coord._download(0, {"device_id": "cam0", "alias": "F"}, clip))
        return asked

    def test_a_clip_already_on_disk_is_not_fetched_again(self):
        """Each fetch is a whole media session against a hub that wedges."""
        clip = {"startTime": NOW, "endTime": NOW + 15}
        self.assertEqual(self._attempt(clip, existing="/already.ts"), [])

    def test_a_clip_with_no_usable_span_is_not_fetched(self):
        for clip in ({"endTime": NOW + 15}, {"startTime": NOW},
                     {"startTime": NOW, "endTime": NOW},
                     {"startTime": NOW + 15, "endTime": NOW}):
            with self.subTest(clip=clip):
                self.assertEqual(self._attempt(clip), [])

    def test_an_ordinary_clip_is_fetched(self):
        clip = {"startTime": NOW, "endTime": NOW + 15}
        self.assertEqual(len(self._attempt(clip)), 1)


class AClipThatDoesNotDecode(unittest.TestCase):
    """Verified now, while the hub still holds the original.

    A truncated file looks identical to a good one on disk -- same name, same
    place, plausible size -- and the only moment it can be fetched again is
    before retention evicts the source. Discovering it later means the
    recording is simply gone.
    """

    def _download(self, decodes, failures_before=0):
        coord, _ = harness._build()
        if failures_before:
            coord._download_failures[0] = failures_before
        module = sys.modules["tapo_h500.coordinator"]
        removed = []

        class Stored:
            name = "224640.ts"

            @staticmethod
            def unlink(missing_ok=False):
                removed.append(True)

        async def download(*args, **kwargs):
            return {"path": "/x.ts", "bytes": 4096}

        async def verify(hass, path):
            return decodes

        async def pruned(*args, **kwargs):
            return []

        for name, value in (("existing_clip",
                             lambda hass, camera, start: Stored()),
                            ("async_download_clip", download),
                            ("async_verify", verify),
                            ("async_prune", pruned)):
            self.addCleanup(setattr, module, name, getattr(module, name, None))
            setattr(module, name, value)
        # existing_clip answers for the check after the download as well as
        # the one before it, so the skip has to be stepped over deliberately.
        coord._seen_clips[0] = {(NOW,)}
        original_skip = module.existing_clip
        calls = {"n": 0}

        def once(hass, camera, start):
            calls["n"] += 1
            return None if calls["n"] == 1 else Stored()

        module.existing_clip = once
        run(coord._download(0, {"device_id": "cam0", "alias": "F"},
                            {"startTime": NOW, "endTime": NOW + 15}))
        module.existing_clip = original_skip
        return coord, removed

    def test_a_file_that_does_not_decode_is_removed_so_it_can_be_refetched(self):
        coord, removed = self._download(decodes=False)
        self.assertEqual(removed, [True])
        self.assertNotIn((NOW,), coord._seen_clips.get(0, set()),
                         "forgotten, so the next poll fetches it again")

    def test_it_counts_as_the_pipeline_failing(self):
        """Bytes arrived and did not decode. The media-problem sensor has to
        see that, or a camera writing rubbish reads as working."""
        coord, _ = self._download(decodes=False)
        self.assertEqual(coord._download_failures.get(0), 1)

    def test_a_file_that_decodes_is_kept(self):
        coord, removed = self._download(decodes=True)
        self.assertEqual(removed, [])

    def test_one_good_download_clears_the_run_of_failures(self):
        """The media-problem sensor reads this count, so a camera that has
        started working again has to stop being reported as broken."""
        coord, _ = self._download(decodes=True, failures_before=3)
        self.assertNotIn(0, coord._download_failures)

    def test_a_decode_failure_adds_to_the_run_rather_than_resetting_it(self):
        coord, _ = self._download(decodes=False, failures_before=3)
        self.assertEqual(coord._download_failures.get(0), 4)


class AClipTheHubWillNotServe(unittest.TestCase):
    """Indexed but empty: the hub lists a recording and then streams nothing.

    Its own bookkeeping, separate from a transport failure, because it is not
    a broken pipeline -- it is one clip the hub cannot produce, and retrying
    it forever costs a media session each time on a device that wedges.
    """

    def _download(self, error):
        coord, _ = harness._build()
        module = sys.modules["tapo_h500.coordinator"]

        async def refuse(*args, **kwargs):
            raise error

        for name, value in (("existing_clip",
                             lambda hass, camera, start: None),
                            ("async_download_clip", refuse)):
            self.addCleanup(setattr, module, name, getattr(module, name, None))
            setattr(module, name, value)
        run(coord._download(0, {"device_id": "cam0", "alias": "F"},
                            {"startTime": NOW, "endTime": NOW + 15}))
        return coord

    def test_an_empty_recording_is_remembered_so_it_is_not_retried_forever(self):
        coord = self._download(
            sys.modules["tapo_h500.media"].EmptyRecordingError("no bytes"))
        self.assertIn(NOW, coord._failed_clips.get(0, {}))

    def test_an_empty_recording_counts_against_the_camera(self):
        coord = self._download(
            sys.modules["tapo_h500.media"].EmptyRecordingError("no bytes"))
        self.assertEqual(coord._download_failures.get(0), 1)

    def test_only_an_empty_recording_is_logged_as_an_empty_one(self):
        """EmptyRecordingError is a HomeAssistantError, so both land in the
        same shape of handler. What separates them is the media log: a hub
        that answers every session and carries no video is a different
        problem from one that will not answer, and the health sensor is how
        anybody sees which is happening."""
        media = sys.modules["tapo_h500.media"]
        from homeassistant.exceptions import HomeAssistantError
        empty = self._download(media.EmptyRecordingError("no bytes"))
        self.assertEqual(empty.media._empty, 1)
        stalled = self._download(HomeAssistantError("stream stalled"))
        self.assertEqual(stalled.media._empty, 0)

    def test_a_transport_failure_is_recorded_the_same_way(self):
        from homeassistant.exceptions import HomeAssistantError
        coord = self._download(HomeAssistantError("stream stalled"))
        self.assertEqual(coord._download_failures.get(0), 1)
        self.assertIn(NOW, coord._failed_clips.get(0, {}))

    def test_neither_takes_the_poll_down_with_it(self):
        """One clip failing must not stop the others being fetched."""
        from homeassistant.exceptions import HomeAssistantError
        for error in (HomeAssistantError("stalled"),
                      sys.modules["tapo_h500.media"].EmptyRecordingError("x")):
            with self.subTest(error=type(error).__name__):
                self._download(error)  # returns rather than raising


class TheContactSheetEntity(unittest.TestCase):
    def _sheet(self):
        image_mod = importlib.import_module("tapo_h500.image")
        coord, _ = harness._build()
        hass = harness._Hass()
        entity = image_mod.H500ContactSheet(
            hass, coord, 1, {"device_id": "cam1", "alias": "Side"})
        entity.hass = hass
        entity.async_write_ha_state = lambda: None
        entity.async_on_remove = lambda unsub: None
        return image_mod, entity

    def test_it_follows_the_download_signal_not_the_event_one(self):
        """A sheet is built from thumbnails and a thumbnail is written by the
        download. Stamping on the event would make the frontend re-fetch an
        unchanged picture seconds before the new frame exists."""
        image_mod, entity = self._sheet()
        seen = []
        original = image_mod.async_dispatcher_connect
        image_mod.async_dispatcher_connect = (
            lambda hass, signal, target: seen.append(signal) or (lambda: None))
        try:
            run(entity.async_added_to_hass())
        finally:
            image_mod.async_dispatcher_connect = original
        self.assertEqual(len(seen), 1)
        self.assertIn("image", seen[0])
        self.assertNotIn("event", seen[0])

    def test_a_new_download_restamps_it(self):
        """Home Assistant fetches only when the stamp changes, which is why
        the picture is built on request rather than held in memory."""
        _, entity = self._sheet()
        self.assertIsNone(entity.image_last_updated)
        entity._handle()
        self.assertIsNotNone(entity.image_last_updated)


class TheSirenToneSelect(unittest.TestCase):
    def _added(self, tones):
        select_mod = importlib.import_module("tapo_h500.select")
        coord, client = harness._build()
        if isinstance(tones, Exception):
            def refuse():
                raise tones
            client.siren_tones = refuse
        else:
            client.siren_tones = lambda: list(tones)
        coord.client = client
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
        added = []
        entry = harness._Entry(20)
        entry.async_on_unload = lambda unsub: None
        run(select_mod.async_setup_entry(hass, entry, added.extend))
        return added

    def test_the_hubs_own_list_becomes_the_choices(self):
        added = self._added(["Doorbell", "Alarm"])
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].options, ["Doorbell", "Alarm"])

    def test_a_hub_that_offers_no_tones_gets_no_entity(self):
        """There is nothing to choose between, and a free text box would just
        produce -40209s."""
        self.assertEqual(self._added([]), [])

    def test_a_hub_that_will_not_say_gets_no_entity_either(self):
        """Not a failed setup: everything else about the hub still works."""
        self.assertEqual(self._added(RuntimeError("-40209")), [])


class TheLastFewRules(unittest.TestCase):
    """Small functions with one decision each, and the decision is the test."""

    def test_the_alarm_field_outranks_the_detection_list(self):
        """alarm_type reports the most significant thing that happened, and
        the hub says which that was when it says anything at all."""
        clips_mod = importlib.import_module("tapo_h500.clips")
        entry = {"alarm_type": 17, "events_1": (1 << 1) | (1 << 5)}
        self.assertEqual(clips_mod.primary_type(entry), 17)

    def test_with_no_alarm_field_the_last_detection_stands_in(self):
        clips_mod = importlib.import_module("tapo_h500.clips")
        entry = {"events_1": (1 << 1) | (1 << 5)}
        self.assertEqual(clips_mod.primary_type(entry), 6)

    def test_an_entry_with_neither_has_no_type(self):
        clips_mod = importlib.import_module("tapo_h500.clips")
        self.assertIsNone(clips_mod.primary_type({}))

    def test_unknown_faces_are_counted_by_their_own_code(self):
        """Code 22 is "a face, matched to nobody" -- the one that makes a
        stranger at the door different from a stranger in the street."""
        clips_mod = importlib.import_module("tapo_h500.clips")
        seen = [clip(NOW, mask=1 << 21), clip(NOW + 30, mask=1 << 19),
                clip(NOW + 60, mask=1 << 21), clip(NOW + 90, mask=1 << 1)]
        self.assertEqual(clips_mod.unknown_face_count(seen), 2)


class WhatTheMediaPortSays(unittest.TestCase):
    """The three answers, which are how the wedge is told from a hub that is
    merely unreachable -- and the reason this probe exists at all."""

    def _check(self, behaviour):
        api_mod = importlib.import_module("tapo_h500.api")
        socket_mod = sys.modules["socket"]

        class Probe:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def settimeout(self, _seconds):
                return None

            def sendall(self, _request):
                if isinstance(behaviour, Exception):
                    raise behaviour

            def recv(self, _size):
                if isinstance(behaviour, Exception):
                    raise behaviour
                return behaviour

        original = socket_mod.create_connection
        socket_mod.create_connection = (
            lambda *args, **kwargs: (_ for _ in ()).throw(behaviour)
            if isinstance(behaviour, OSError)
            and not isinstance(behaviour, (TimeoutError, ConnectionError))
            else Probe())
        try:
            return api_mod.check_media_port("192.168.11.5")
        finally:
            socket_mod.create_connection = original

    def test_a_hub_that_answers_is_healthy(self):
        self.assertEqual(self._check(b"\x00\x01"), "healthy")

    def test_a_hub_that_accepts_and_says_nothing_is_wedged(self):
        """The shape of the wedge: the port is open, the handshake never
        comes, and it recovers on a timeout rather than on a retry."""
        self.assertEqual(self._check(b""), "wedged")

    def test_no_answer_at_all_is_silent_not_wedged(self):
        for error in (TimeoutError("timed out"),):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self._check(error), "silent")

    def test_a_reset_mid_exchange_reads_as_wedged_too(self):
        """The hub closes on the request instead of answering it; depending
        on timing that is an empty read or a reset, and both mean the same
        thing."""
        self.assertEqual(self._check(ConnectionError("reset")), "wedged")

    def test_a_hub_that_never_accepted_is_unreachable(self):
        """A different answer from all three: nothing was reached, so
        nothing can be said about whether it is wedged."""
        self.assertEqual(self._check(OSError("no route to host")),
                         "unreachable")
