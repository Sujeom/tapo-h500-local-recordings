"""Everyone at once, instead of one sensor per person compared by eye.

One entity per person is the right shape for automating and the wrong one for
looking at: with five people named, "is anybody about" means reading five
sensors and five timestamps and doing the arithmetic yourself.

The honesty of the wording matters more than usual here. A camera watches a
doorstep, not a house -- somebody indoors is invisible to it, and so is
somebody who left through a door with no camera on it. `not_seen` is a list of
people who have not been seen, and is deliberately not a list of people who
are out.
"""
import asyncio
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "sensor.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
clips_mod = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
WINDOW = const.FACE_PRESENCE_WINDOW


def sighting(face_id, when):
    return {"startTime": when, "endTime": when + 10,
            "events_1": 1 << (20 - 1),
            "event_info": [{"face_id": face_id}]}


def earlier_today(hours=6):
    """A moment that many hours ago, pulled forward if it lands on yesterday.

    A fixed offset is not portable: six hours before NOW is the previous local
    day in half the world's timezones, and "seen today" would then be asserted
    against a sighting that was not from today.
    """
    today = clips_mod.local_date(NOW)
    for back in range(hours, 0, -1):
        if clips_mod.local_date(NOW - back * 3600) == today:
            return NOW - back * 3600
    return NOW - 60


def yesterday():
    when = NOW
    while clips_mod.local_date(when) == clips_mod.local_date(NOW):
        when -= 3600
    return when


class _Client:
    def __init__(self):
        self.clips = []

    def cameras(self):
        return [{"device_id": "cam0", "alias": "Front"}]

    def recent(self, camera, start, end):
        return list(self.clips)

    detections = recent

    def hub_status(self):
        return {}


def build(names=None, clips=()):
    client = _Client()
    client.clips = list(clips)
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20, face_names=names or {}), client)
    coord._download_new = lambda *a, **k: None
    coord.data = asyncio.run(coord._async_update_data())
    return coord


class Household(unittest.TestCase):
    NAMES = {"11": "Alice", "22": "Bob", "33": "Carol"}

    def test_somebody_seen_just_now_is_seen_recently(self):
        coord = build(self.NAMES, [sighting(11, NOW - 60)])
        self.assertEqual(coord.household(WINDOW)["seen_recently"], ["Alice"])

    def test_everyone_else_is_listed_as_not_seen(self):
        coord = build(self.NAMES, [sighting(11, NOW - 60)])
        self.assertEqual(coord.household(WINDOW)["not_seen"], ["Bob", "Carol"])

    def test_somebody_seen_hours_ago_is_not_seen_recently(self):
        coord = build(self.NAMES, [sighting(11, earlier_today())])
        household = coord.household(WINDOW)
        self.assertEqual(household["seen_recently"], [])
        self.assertIn("Alice", household["not_seen"])

    def test_but_they_were_seen_today(self):
        """The difference between "not here this minute" and "has not been
        home all day", which is the one worth knowing."""
        coord = build(self.NAMES, [sighting(11, earlier_today())])
        self.assertEqual(coord.household(WINDOW)["seen_today"], ["Alice"])

    def test_last_night_is_not_today(self):
        """The window reaches back a full day, so at one in the morning it
        still holds last night."""
        coord = build(self.NAMES, [sighting(11, yesterday())])
        self.assertEqual(coord.household(WINDOW)["seen_today"], [])

    def test_the_day_is_the_local_one(self):
        """NOW is 05:46 UTC and still the previous evening at -07:00. A UTC
        day boundary would report last night as today."""
        self.assertEqual(clips_mod.local_date(NOW), "2026-08-12")

    def test_an_unnamed_face_is_nobody(self):
        """The hub invents an id for every passer-by. Counting those would
        make the number a measure of how busy the street is."""
        coord = build(self.NAMES, [sighting(481036337152, NOW - 60)])
        self.assertEqual(coord.household(WINDOW)["seen_recently"], [])

    def test_a_person_clustered_twice_is_counted_once(self):
        coord = build({"11": "Alice", "22": "Alice"},
                      [sighting(11, NOW - 90), sighting(22, NOW - 60)])
        self.assertEqual(coord.household(WINDOW)["seen_recently"], ["Alice"])

    def test_either_of_their_clusters_counts_as_seeing_them(self):
        """The first cluster last saw Alice yesterday and the second saw her
        a minute ago. Reading one cluster instead of the merged person reports
        her as not seen, and picks whichever cluster happens to sort first --
        so the same house answers differently depending on the ids the hub
        invented.
        """
        coord = build({"11": "Alice", "22": "Alice"},
                      [sighting(11, yesterday()), sighting(22, NOW - 60)])
        self.assertEqual(coord.household(WINDOW)["seen_recently"], ["Alice"])
        self.assertEqual(coord.household(WINDOW)["not_seen"], [])

    def test_nobody_named_is_three_empty_lists(self):
        """Rather than an error, and distinguishable from an empty house by
        the `named` attribute beside it."""
        coord = build({}, [sighting(481036337152, NOW - 60)])
        self.assertEqual(coord.household(WINDOW),
                         {"seen_recently": [], "seen_today": [], "not_seen": []})

    def test_the_window_is_what_decides(self):
        coord = build(self.NAMES, [sighting(11, NOW - 300)])
        self.assertEqual(coord.household(600)["seen_recently"], ["Alice"])
        self.assertEqual(coord.household(120)["seen_recently"], [])


class Entity(unittest.TestCase):
    def test_the_state_is_how_many_were_seen(self):
        body = SOURCE.split("class H500Household", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('household(\n            FACE_PRESENCE_WINDOW)["seen_recently"]',
                      body)

    def test_it_lists_who_has_been_named_as_well(self):
        """An empty house and an installation where nobody has been named yet
        would otherwise both read zero."""
        body = SOURCE.split("class H500Household", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('"named": sorted(self.coordinator.named_people)', body)

    def test_it_says_what_window_it_used(self):
        body = SOURCE.split("class H500Household", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('"window_minutes"', body)

    def test_it_is_not_called_home_or_present(self):
        """Not being seen is not evidence of absence, and a name that claims
        otherwise is what gets built on."""
        body = SOURCE.split("class H500Household", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('_attr_translation_key = "people_seen_recently"', body)
        label = STRINGS["entity"]["sensor"]["people_seen_recently"]["name"]
        self.assertNotIn("home", label.lower())

    def test_it_is_on_the_hub_not_a_camera(self):
        body = SOURCE.split("class H500Household", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("hub_device(coordinator, entry)", body)

    def test_it_is_added_once(self):
        self.assertEqual(SOURCE.count("H500Household(coordinator, entry)"), 1)


if __name__ == "__main__":
    unittest.main()
