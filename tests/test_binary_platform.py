"""The binary sensors, constructed and flipped.

binary_sensor.py sat at 59%: the flag tables, the detection holds, the
worked-out signals and the per-person presence had never been driven. What a
string match cannot see is the part that matters here -- whether the person
flag actually turns on for a person, holds, and lets go.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

binary = importlib.import_module("tapo_h500.binary_sensor")
const = importlib.import_module("tapo_h500.const")
dt_util = sys.modules["homeassistant.util.dt"]
event_helper = sys.modules["homeassistant.helpers.event"]
dispatcher = sys.modules["homeassistant.helpers.dispatcher"]

NOW = int(dt_util.utcnow().timestamp())
CAMERA = {"device_id": "cam0", "alias": "Front"}


def clip(start, mask=1 << 1):
    return {"startTime": start, "endTime": start + 15, "events_1": mask}


def _wire(entity):
    entity.hass = harness._Hass()
    entity.writes = 0
    entity.async_write_ha_state = lambda: setattr(
        entity, "writes", entity.writes + 1)
    entity.async_on_remove = lambda unsub: None
    return entity


class TheFlagTables(unittest.TestCase):
    def _hub_flag(self, key, **readings):
        coord, _ = harness._build()
        coord.readings = readings
        description = next(d for d in binary.HUB_FLAGS if d.key == key)
        return binary.H500HubFlag(coord, harness._Entry(20), description)

    def test_the_storage_problem_is_inverted_health(self):
        """PROBLEM is on when unhealthy; a healthy disk reads off."""
        self.assertIs(self._hub_flag("storage_problem",
                                     storage_healthy=False).is_on, True)
        self.assertIs(self._hub_flag("storage_problem",
                                     storage_healthy=True).is_on, False)

    def test_unknown_storage_is_unknown_not_fine(self):
        self.assertIsNone(self._hub_flag("storage_problem").is_on)

    def test_a_camera_flag_reads_the_live_record_not_the_snapshot(self):
        """The paired list is refreshed every poll; the record given at
        construction goes stale the first time a setting changes."""
        coord, _ = harness._build()
        stale = {"device_id": "cam0", "hub_storage_enabled": False}
        coord.cameras = [{"device_id": "cam0", "hub_storage_enabled": True}]
        description = next(d for d in binary.CAMERA_FLAGS
                           if d.key == "hub_storage")
        entity = binary.H500CameraFlag(coord, 0, stale, description)
        self.assertIs(entity.is_on, True)

    def test_a_camera_that_left_the_list_falls_back_to_its_snapshot(self):
        coord, _ = harness._build()
        coord.cameras = []
        description = next(d for d in binary.CAMERA_FLAGS
                           if d.key == "hub_storage")
        entity = binary.H500CameraFlag(
            coord, 0, {"device_id": "cam0", "hub_storage_enabled": True},
            description)
        self.assertIs(entity.is_on, True)


class TheDetectionHold(unittest.TestCase):
    def _flag(self, code=6):
        coord, _ = harness._build()
        entity = _wire(binary.H500DetectionFlag(coord, 0, CAMERA, code))
        return entity

    def test_its_own_code_turns_it_on_and_others_do_not(self):
        entity = self._flag(code=6)
        entity._handle("motion", {"events_1": 1 << 1})
        self.assertFalse(entity._attr_is_on, "plain motion is not a person")
        entity._handle("motion", {"events_1": (1 << 1) | (1 << 5)})
        self.assertTrue(entity._attr_is_on)

    def test_it_lets_go_by_itself_after_the_hold(self):
        """The hub reports that something happened and never that it
        stopped."""
        event_helper.timers.clear()
        entity = self._flag()
        entity._handle("motion", {"events_1": 1 << 5})
        timer = event_helper.timers[-1]
        self.assertEqual(timer["delay"], const.DETECTION_HOLD)
        timer["action"](None)
        self.assertFalse(entity._attr_is_on)

    def test_a_visitor_who_keeps_triggering_reads_as_one_presence(self):
        """The second detection restarts the hold rather than letting the
        first one's timer end it mid-visit."""
        event_helper.timers.clear()
        entity = self._flag()
        entity._handle("motion", {"events_1": 1 << 5})
        first = event_helper.timers[-1]
        entity._handle("motion", {"events_1": 1 << 5})
        self.assertTrue(first["cancelled"])
        self.assertTrue(entity._attr_is_on)


class TheWorkedOutSignals(unittest.TestCase):
    def _entity(self, cls, clips):
        coord, _ = harness._build()
        coord.clips_for = lambda index: list(clips)
        # Pinned rather than read from options: the thresholds are their own
        # tested model (test_sensitivity), and this file is about the entity
        # wiring around them staying deterministic if the defaults move.
        coord.sensitivity = lambda index: (2.0, 3)
        return cls(coord, 0, CAMERA), coord

    def test_a_quiet_camera_is_not_unusual(self):
        entity, _ = self._entity(binary.H500UnusualActivity,
                                 [clip(NOW - 7200)])
        self.assertFalse(entity.is_on)
        attributes = entity.extra_state_attributes
        self.assertEqual(attributes["events_last_hour"], 0)
        self.assertIn("multiplier", attributes)

    def test_a_burst_against_a_quiet_baseline_is(self):
        """One clip every few hours all day, then nine in the last hour:
        well past twice the baseline and past the floor."""
        quiet_day = [clip(NOW - 3600 * n) for n in range(3, 20)]
        burst = [clip(NOW - 60 * n) for n in range(1, 10)]
        entity, _ = self._entity(binary.H500UnusualActivity,
                                 quiet_day + burst)
        self.assertTrue(entity.is_on)

    # Loitering is specifically an UNRECOGNISED face -- code 22. A named
    # face waiting is somebody coming home, and plain motion is a cat.
    UNKNOWN_FACE = 1 << 21

    def test_a_wait_at_the_door_is_loitering_and_says_how_long(self):
        stay = [clip(NOW - offset, self.UNKNOWN_FACE)
                for offset in (240, 180, 120, 60)]
        entity, _ = self._entity(binary.H500Loitering, stay)
        self.assertTrue(entity.is_on)
        self.assertGreaterEqual(entity.extra_state_attributes["seconds"],
                                const.LOITER_SECONDS)

    def test_a_passer_by_is_not(self):
        entity, _ = self._entity(binary.H500Loitering,
                                 [clip(NOW - 60, self.UNKNOWN_FACE)])
        self.assertFalse(entity.is_on)
        self.assertEqual(entity.extra_state_attributes["seconds"], 0,
                         "zero, not absent: templates get a number")

    def test_a_known_face_waiting_is_not_loitering(self):
        """Somebody coming home stands at their own door."""
        stay = [clip(NOW - offset) for offset in (240, 180, 120, 60)]
        entity, _ = self._entity(binary.H500Loitering, stay)
        self.assertFalse(entity.is_on)

    def _stay_of_exactly_the_minimum(self, ending):
        """Sightings close enough to be one visit, spanning LOITER_SECONDS
        exactly -- first start to last end, which is what a visit measures."""
        first = ending - const.LOITER_SECONDS
        return [clip(start, self.UNKNOWN_FACE)
                for start in (first, first + 85, ending - 15)]

    def test_a_wait_exactly_as_long_as_the_minimum_counts(self):
        """LOITER_SECONDS means "at least this", and the person who stood
        there for exactly three minutes is the case the constant names."""
        entity, _ = self._entity(binary.H500Loitering,
                                 self._stay_of_exactly_the_minimum(NOW))
        self.assertTrue(entity.is_on)
        self.assertEqual(entity.extra_state_attributes["seconds"],
                         const.LOITER_SECONDS)

    def test_a_visit_that_ended_exactly_a_gap_ago_is_still_open(self):
        """The gap is what joins two sightings into one visit, so a visit is
        over only once MORE than a gap has passed -- otherwise someone still
        standing there flickers off between recordings."""
        entity, _ = self._entity(
            binary.H500Loitering,
            self._stay_of_exactly_the_minimum(NOW - const.LOITER_GAP))
        self.assertTrue(entity.is_on)

    def test_a_wait_that_ended_hours_ago_is_over(self):
        gone = [clip(NOW - 7500, self.UNKNOWN_FACE),
                clip(NOW - 7260, self.UNKNOWN_FACE)]
        entity, _ = self._entity(binary.H500Loitering, gone)
        self.assertFalse(entity.is_on)


class Prowling(unittest.TestCase):
    def _sensor(self, everyone):
        coord, _ = harness._build()
        coord.everyone = lambda: list(everyone)
        return binary.H500Prowling(coord, harness._Entry(20))

    def test_a_circuit_turns_it_on_and_names_who(self):
        sensor = self._sensor([
            {"id": "7", "name": "Alice", "prowling": True, "trail": []},
            {"id": "8", "name": None, "prowling": False, "trail": []},
        ])
        self.assertTrue(sensor.is_on)
        faces = sensor.extra_state_attributes["faces"]
        self.assertEqual(len(faces), 1)

    def test_ordinary_visitors_leave_it_off(self):
        sensor = self._sensor([
            {"id": "7", "name": None, "prowling": False, "trail": []}])
        self.assertFalse(sensor.is_on)


class SeenRecently(unittest.TestCase):
    def _sensor(self, last_seen, names=None):
        coord, _ = harness._build()
        coord.person_for = lambda face_id: (
            {"last_seen": last_seen} if last_seen else {})
        if names:
            coord.entry.options = {**coord.entry.options,
                                   "face_names": names}
        return binary.H500FaceSeenRecently(coord, harness._Entry(20), "7")

    def test_seen_within_the_window_is_on(self):
        self.assertTrue(self._sensor(NOW - 60).is_on)

    def test_seen_this_morning_is_off_not_away(self):
        self.assertFalse(
            self._sensor(NOW - const.FACE_PRESENCE_WINDOW - 1).is_on)

    def test_never_seen_is_off(self):
        self.assertFalse(self._sensor(None).is_on)

    def test_it_is_named_for_the_person(self):
        self.assertEqual(self._sensor(NOW, names={"7": "Alice"}).name,
                         "Alice seen recently")
        self.assertEqual(self._sensor(NOW).name, "Face 7 seen recently")


class Setup(unittest.TestCase):
    def _setup(self, cameras=1, names=None):
        coord, _ = harness._build()
        coord.cameras = [{"device_id": f"cam{n}", "alias": f"C{n}"}
                         for n in range(cameras)]
        if names:
            coord.entry.options = {**coord.entry.options,
                                   "face_names": names}
        hass = harness._Hass()
        hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
        hass.config_entries = harness._ConfigEntries(
            [coord.entry])
        coord.entry.runtime_data = coord
        added = []
        entry = coord.entry
        asyncio.run(binary.async_setup_entry(hass, entry, added.extend))
        return added, coord

    def test_one_camera_gets_the_full_complement(self):
        added, _ = self._setup()
        # 2 hub flags + 4 camera flags + unusual + loitering + silent +
        # delivery + prowling + media + dark + 9 detections = 22.
        self.assertEqual(len(added), 22)

    def test_two_clusters_of_one_person_are_one_presence_entity(self):
        """Keyed on the lowest id of the group, so naming both halves of a
        hub double-cluster does not make two sensors called Alice."""
        added, _ = self._setup(names={"7": "Alice", "9": "Alice"})
        presence = [e for e in added
                    if isinstance(e, binary.H500FaceSeenRecently)]
        self.assertEqual(len(presence), 1)
        self.assertEqual(presence[0].face_id, "7")

    def test_the_faces_signal_firing_again_adds_nobody_twice(self):
        """_sync_faces re-runs on every rename. Without the already-added
        guard, each rename would register another sensor called Alice."""
        connected = []
        original = binary.async_dispatcher_connect

        def record(hass, signal, target):
            connected.append(target)
            return lambda: None

        binary.async_dispatcher_connect = record
        self.addCleanup(setattr, binary, "async_dispatcher_connect", original)
        added, _ = self._setup(names={"7": "Alice"})
        before = len([e for e in added
                      if isinstance(e, binary.H500FaceSeenRecently)])
        connected[-1]()          # the rename signal fires again
        after = len([e for e in added
                     if isinstance(e, binary.H500FaceSeenRecently)])
        self.assertEqual((before, after), (1, 1))


class TheDetectionFlagsWiring(unittest.TestCase):
    """What a flag subscribes to, and what it lets go of."""

    def _added(self, code=6):
        coord, _ = harness._build()
        entity = _wire(binary.H500DetectionFlag(coord, 1, CAMERA, code))
        self.removals = []
        entity.async_on_remove = self.removals.append
        signals = []
        original = binary.async_dispatcher_connect
        binary.async_dispatcher_connect = (
            lambda hass, signal, target: signals.append(signal) or (lambda: None))
        try:
            asyncio.run(entity.async_added_to_hass())
        finally:
            binary.async_dispatcher_connect = original
        return entity, signals

    def test_it_listens_to_its_own_cameras_events(self):
        _, signals = self._added()
        self.assertEqual(len(signals), 1)
        self.assertTrue(signals[0].endswith("_1"),
                        "camera 1's signal, not camera 0's")

    def test_removal_cancels_a_pending_hold(self):
        """A timer firing against a removed entity raises, and the hold can
        outlive the entity by minutes."""
        event_helper.timers.clear()
        entity, _ = self._added()
        entity._handle("motion", {"events_1": 1 << 5})
        pending = event_helper.timers[-1]
        for unsubscribe in self.removals:
            unsubscribe()
        self.assertTrue(pending["cancelled"])

    def test_removing_one_that_never_fired_is_harmless(self):
        entity, _ = self._added()
        for unsubscribe in self.removals:
            unsubscribe()
        self.assertIsNone(entity._clear_timer)


class ThePossibleDelivery(unittest.TestCase):
    """A guess, and named like one: somebody was there, the hub did not
    recognise them, and they did not stay. Retrospective on purpose -- at the
    moment a detection arrives, the person has been there for one clip, and
    so has everybody who is about to stay for ten minutes."""

    PERSON = 1 << 5
    KNOWN = 1 << 19

    def _entity(self, clips, hour=14):
        coord, _ = harness._build()
        coord.clips_for = lambda index: list(clips)
        coord.entry.options = {**coord.entry.options,
                               "night_start": 22, "night_end": 6}
        entity = _wire(binary.H500Delivery(
            coord, 0, CAMERA))
        self._at_hour(hour)
        return entity

    def _at_hour(self, hour):
        """Move the frozen clock's local hour without moving NOW."""
        original = dt_util.as_local
        local_now = original(dt_util.utc_from_timestamp(NOW))
        dt_util.as_local = lambda moment: local_now.replace(hour=hour)
        self.addCleanup(setattr, dt_util, "as_local", original)

    def _visit(self, ended_ago, lasted, mask=None):
        """An unrecognised visit of `lasted` seconds that finished
        `ended_ago` seconds back.

        A chain, not two clips: sightings further apart than LOITER_GAP are
        two visits rather than one long one, so a fixture built from its
        endpoints alone measures fifteen seconds however far apart it puts
        them.
        """
        mask = self.PERSON if mask is None else mask
        end = NOW - ended_ago
        starts = list(range(end - lasted, end - 15, 85)) + [end - 15]
        return [clip(start, mask) for start in starts]

    def test_a_short_unrecognised_visit_just_ended_reads_as_one(self):
        entity = self._entity(self._visit(ended_ago=200, lasted=30))
        self.assertTrue(entity.is_on)

    def test_a_visit_still_happening_is_not_one_yet(self):
        """Its length is not final while it is going on, and that is the
        whole reason this is retrospective."""
        entity = self._entity(self._visit(ended_ago=10, lasted=30))
        self.assertFalse(entity.is_on)

    def test_somebody_who_stayed_is_not_a_courier(self):
        entity = self._entity(
            self._visit(ended_ago=200, lasted=const.DELIVERY_SECONDS + 120))
        self.assertFalse(entity.is_on)

    def test_a_visit_long_over_is_no_longer_news(self):
        """It stays true for a while so an automation has time to see it,
        and then stops."""
        entity = self._entity(
            self._visit(ended_ago=const.DELIVERY_HOLD + 60, lasted=30))
        self.assertFalse(entity.is_on)

    def test_a_face_the_hub_knows_is_somebody_in_a_hurry(self):
        """Recognised at any point during the visit: a member of the
        household arriving and leaving quickly looks exactly like this."""
        known = self._visit(ended_ago=200, lasted=30,
                            mask=self.PERSON | self.KNOWN)
        self.assertFalse(self._entity(known).is_on)

    def test_nothing_at_the_door_is_not_a_delivery(self):
        self.assertFalse(self._entity([]).is_on)

    def test_nobody_delivers_at_three_in_the_morning(self):
        """In daylight a quick unrecognised visit is a courier far more often
        than not. At night it is the same shape and means something else."""
        entity = self._entity(self._visit(ended_ago=200, lasted=30), hour=3)
        self.assertFalse(entity.is_on)


class TheSilentHoursSetting(unittest.TestCase):
    """Typed in by hand, so it arrives as whatever the box produced."""

    def _seconds(self, value):
        coord, _ = harness._build()
        coord.entry.options = {**coord.entry.options, "silent_hours": value}
        return binary.silent_threshold(coord)

    def test_a_number_typed_as_text_still_counts(self):
        self.assertEqual(self._seconds("4"), 4 * 3600)

    def test_something_that_is_not_a_number_falls_back_to_the_default(self):
        """A silence alarm that never fires is one nobody notices is
        broken."""
        for value in ("soon", None, [], {}):
            with self.subTest(value=value):
                self.assertEqual(self._seconds(value),
                                 const.DEFAULT_SILENT_HOURS * 3600)

    def test_it_never_drops_below_an_hour(self):
        """Anything shorter alarms on an ordinary quiet stretch."""
        self.assertEqual(self._seconds(0), 3600)

    def test_it_never_asks_for_more_history_than_is_kept(self):
        self.assertEqual(self._seconds(10_000), const.LOOKBACK_SECONDS)


if __name__ == "__main__":
    unittest.main()
