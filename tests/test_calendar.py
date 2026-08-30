"""The day's detections as calendar entries.

Home Assistant already has a panel built for "what happened last Tuesday", and
the cards do not answer that without knowing to look at last Tuesday first.

The thing worth protecting is where the entries come from. The coordinator
holds a day of recordings; a calendar built on that would show one day and
then nothing, and scrolling back would suggest a quiet fortnight rather than
an absent one. These entries come from the hub, which keeps weeks.
"""
import asyncio
import datetime
import importlib
import sys
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
CALENDAR_SOURCE = (COMPONENT / "calendar.py").read_text()
INIT = (COMPONENT / "__init__.py").read_text()
# The thirteen service handlers moved out of the package body.
SERVICES_SRC = (COMPONENT / "services.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)


class _CalendarEvent:
    def __init__(self, start, end, summary, description=None, location=None):
        self.start = start
        self.end = end
        self.summary = summary
        self.description = description
        self.location = location


def _stub_calendar():
    module = types.ModuleType("homeassistant.components.calendar")
    module.CalendarEntity = type("CalendarEntity", (), {})
    module.CalendarEvent = _CalendarEvent
    sys.modules.setdefault("homeassistant.components.calendar", module)

    registry = types.ModuleType("homeassistant.helpers.device_registry")
    registry.DeviceInfo = dict
    sys.modules.setdefault("homeassistant.helpers.device_registry", registry)

    platform = types.ModuleType("homeassistant.helpers.entity_platform")
    platform.AddEntitiesCallback = object
    sys.modules.setdefault("homeassistant.helpers.entity_platform", platform)

    # entity.py needs a CoordinatorEntity to inherit from. The stub harness
    # only supplies the coordinator base, which is a different class.
    updates = sys.modules["homeassistant.helpers.update_coordinator"]
    if not hasattr(updates, "CoordinatorEntity"):
        class CoordinatorEntity:
            def __init__(self, coordinator):
                self.coordinator = coordinator

            def __class_getitem__(cls, item):
                return cls
        updates.CoordinatorEntity = CoordinatorEntity


_stub_calendar()
calendar = importlib.import_module("tapo_h500.calendar")
coordinator_mod = importlib.import_module("tapo_h500.coordinator")

NOW = 1_786_600_000
CAMERA = {"device_id": "cam0", "alias": "Front Doorbell", "mac": "aa"}
PRESS = (1 << 16) | (1 << 5) | (1 << 9)          # codes 17, 6, 10
FACE = 1 << 19                                    # code 20


def detection(ago, mask=PRESS, length=15, face=None):
    entry = {"start_time": NOW - ago, "end_time": NOW - ago + length,
             "events_1": mask}
    if face is not None:
        entry["event_info"] = [{"face_id": face}]
    return entry


class _Client:
    def __init__(self):
        self.asked = []
        self.entries = []
        self.detection_log = True

    def cameras(self):
        return [CAMERA]

    def recent(self, camera, start, end):
        self.asked.append(("recent", start, end))
        return list(self.entries)

    def detections(self, camera, start, end):
        self.asked.append(("detections", start, end))
        return list(self.entries) if self.detection_log else None

    def hub_status(self):
        return {}


def build(names=None):
    client = _Client()
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20, face_names=names or {}), client)
    coord._download_new = lambda *a, **k: None
    coord.cameras = [CAMERA]
    entity = calendar.H500Calendar(coord, 0, CAMERA)
    return entity, coord, client


def window(days=1):
    end = datetime.datetime.fromtimestamp(NOW, datetime.timezone.utc)
    return end - datetime.timedelta(days=days), end


def fetch(entity, days=1):
    start, end = window(days)
    return asyncio.run(entity.async_get_events(harness._Hass(), start, end))


class Source(unittest.TestCase):
    def test_entries_come_from_the_hub_not_the_polled_window(self):
        """The window is a day. A calendar built on it would show one day and
        nothing before, which reads as a quiet fortnight rather than an
        absent one."""
        entity, _, client = build()
        client.entries = [detection(60)]
        fetch(entity, days=7)
        self.assertEqual(client.asked[0][0], "detections")

    def test_the_hub_is_asked_for_the_range_the_panel_wants(self):
        entity, _, client = build()
        fetch(entity, days=3)
        _, start, end = client.asked[0]
        self.assertEqual(end - start, 3 * 86400)

    def test_a_huge_range_is_bounded(self):
        """The panel decides its own range and a year view asks for a year.
        The hub caps at 1000 records, so an unbounded ask silently returns a
        truncated answer and calls it the year."""
        entity, _, client = build()
        fetch(entity, days=400)
        _, start, end = client.asked[0]
        self.assertEqual(end - start, calendar.MAX_SPAN.days * 86400)

    def test_it_falls_back_to_the_clip_index(self):
        """A hub with no detection log still knows when it recorded."""
        entity, _, client = build()
        client.detection_log = False
        client.entries = [detection(60)]
        found = fetch(entity)
        self.assertEqual([name for name, _, _ in client.asked],
                         ["detections", "recent"])
        self.assertEqual(len(found), 1)

    def test_a_hub_that_refuses_gives_an_empty_view(self):
        """Not an error. A calendar that throws takes the whole panel with
        it, and the panel is not where a hub fault should surface."""
        entity, _, client = build()

        def boom(*a):
            raise RuntimeError("hub busy")
        client.detections = boom
        self.assertEqual(fetch(entity), [])


class Entries(unittest.TestCase):
    def test_recordings_with_no_start_are_dropped(self):
        entity, _, client = build()
        client.entries = [detection(60), {"end_time": NOW}]
        self.assertEqual(len(fetch(entity)), 1)

    def test_they_come_back_in_time_order(self):
        entity, _, client = build()
        client.entries = [detection(60), detection(600), detection(300)]
        found = fetch(entity)
        self.assertEqual([item.start for item in found],
                         sorted(item.start for item in found))

    def test_an_entry_with_no_end_still_has_a_length(self):
        """A zero-width block cannot be clicked, so the detail never opens."""
        entity, _, client = build()
        client.entries = [{"start_time": NOW - 60, "events_1": PRESS}]
        found = fetch(entity)[0]
        self.assertGreater(found.end, found.start)

    def test_an_end_before_its_start_is_treated_the_same(self):
        """A hub whose clock moved mid-recording. Trusting it would draw the
        entry backwards."""
        entity, _, client = build()
        client.entries = [{"start_time": NOW - 60, "end_time": NOW - 300,
                           "events_1": PRESS}]
        found = fetch(entity)[0]
        self.assertGreater(found.end, found.start)

    def test_the_summary_says_what_happened(self):
        entity, _, client = build()
        client.entries = [detection(60)]
        self.assertIn("doorbell", fetch(entity)[0].summary.lower())

    def test_the_summary_names_a_recognised_person(self):
        entity, _, client = build({"77": "Alice"})
        client.entries = [detection(60, mask=FACE, face=77)]
        self.assertIn("Alice", fetch(entity)[0].summary)

    def test_an_unnamed_face_is_not_named_in_the_summary(self):
        entity, _, client = build()
        client.entries = [detection(60, mask=FACE, face=481036337152)]
        self.assertNotIn("481036337152", fetch(entity)[0].summary)

    def test_but_its_id_is_in_the_description(self):
        """So somebody chasing a stranger has the number to name them by."""
        entity, _, client = build()
        client.entries = [detection(60, mask=FACE, face=481036337152)]
        self.assertIn("481036337152", fetch(entity)[0].description)

    def test_no_faces_means_no_description_at_all(self):
        entity, _, client = build()
        client.entries = [detection(60)]
        self.assertIsNone(fetch(entity)[0].description)

    def test_the_camera_is_the_location(self):
        entity, _, client = build()
        client.entries = [detection(60)]
        self.assertEqual(fetch(entity)[0].location, "Front Doorbell")


class Current(unittest.TestCase):
    def test_the_newest_polled_recording_is_the_entry(self):
        """A doorbell has no future, and a recording is indexed only once it
        has finished, so "current or next" can only be answered as "latest"."""
        entity, coord, _ = build()
        coord.data = {"clips": {0: [detection(600), detection(60),
                                    detection(300)]}}
        self.assertEqual(entity.event.start.timestamp(), NOW - 60)

    def test_nothing_polled_means_no_entry(self):
        entity, coord, _ = build()
        coord.data = {"clips": {0: []}}
        self.assertIsNone(entity.event)


class Platform(unittest.TestCase):
    def test_the_calendar_platform_is_set_up(self):
        self.assertIn("Platform.CALENDAR", INIT)

    def test_one_per_camera(self):
        setup = CALENDAR_SOURCE.split("async_setup_entry", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn("for index, camera in enumerate(coordinator.cameras)",
                      setup)


if __name__ == "__main__":
    unittest.main()
