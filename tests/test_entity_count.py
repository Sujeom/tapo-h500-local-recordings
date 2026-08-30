"""How many entities a household actually gets, and which are switched on.

A two-camera hub creates 105. Most of a hundred is noise: every one writes to
the recorder on every change, appears in every entity picker, and makes the
ones people use harder to find. Nothing is removed -- each is one checkbox
away in the entity registry -- but the ones nobody automates on start off.

The second half of every test here is the important one. A list that grows
until it hides something people rely on is the way this goes wrong.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

const = importlib.import_module("tapo_h500.const")

PLATFORMS = ("binary_sensor", "button", "calendar", "camera", "event", "image",
             "number", "select", "sensor", "siren", "switch", "update")

# What a doorbell household automates on. None of these may ever default off.
MUST_STAY_ON = (
    "cam0_detected_motion", "cam0_detected_person", "cam0_detected_doorbell",
    "cam0_last_activity", "cam0_visits_24h", "cam0_activity_level",
    "cam0_loitering", "cam0_possible_delivery", "cam0_silent",
    "cam0_contact_sheet", "cam0_latest_event",
)


def _entities():
    coord, client = harness._build()
    coord.cameras = [{"device_id": f"cam{n}", "alias": f"C{n}",
                      "device_model": "TD21"} for n in range(2)]
    client.siren_tones = lambda: ["Doorbell"]
    coord.client = client
    coord.entry.options = {**coord.entry.options, "face_names": {"7": "Sam"}}
    coord.entry.async_on_unload = lambda unsub: None
    hass = harness._hass_with(coord)
    made = []
    for name in PLATFORMS:
        module = importlib.import_module(f"tapo_h500.{name}")
        asyncio.run(module.async_setup_entry(hass, coord.entry, made.extend))
    return made


def _on(entity):
    return getattr(entity, "entity_registry_enabled_default", True)


class WhatATwoCameraHubMakes(unittest.TestCase):
    def setUp(self):
        self.made = _entities()

    def test_the_total_is_what_it_was_measured_at(self):
        """Not a limit -- a tripwire. A platform that suddenly makes twenty
        more should be a decision rather than a surprise."""
        self.assertEqual(len(self.made), 105)

    def test_most_of_them_are_switched_on(self):
        enabled = [entity for entity in self.made if _on(entity)]
        self.assertEqual(len(enabled), 78)

    def test_and_the_rest_are_the_ones_nobody_watches(self):
        off = {entity.unique_id for entity in self.made if not _on(entity)}
        self.assertEqual(len(off), 27)


class TheOnesPeopleUseStayOn(unittest.TestCase):
    """The half that stops this going too far."""

    def setUp(self):
        self.by_id = {entity.unique_id: entity for entity in _entities()}

    def test_nothing_anybody_automates_on_defaults_off(self):
        for unique_id in MUST_STAY_ON:
            with self.subTest(entity=unique_id):
                self.assertIn(unique_id, self.by_id)
                self.assertTrue(_on(self.by_id[unique_id]))

    def test_the_camera_and_its_event_entity_stay_on(self):
        for unique_id in ("cam0_camera", "cam0_activity"):
            with self.subTest(entity=unique_id):
                self.assertTrue(_on(self.by_id[unique_id]))

    def test_the_wedge_diagnostics_stay_on(self):
        """Troubleshooting tells people to read these, so they have to be
        there when somebody goes looking."""
        for key in ("media_sessions", "media_healthy_for", "hub_health"):
            unique_id = f"test_{key}"
            with self.subTest(entity=unique_id):
                self.assertTrue(_on(self.by_id[unique_id]))


class WhatIsOffAndWhy(unittest.TestCase):
    def setUp(self):
        self.made = _entities()

    def test_every_reading_that_is_off_is_on_the_list_saying_so(self):
        """So the choice lives in one place with its reasoning, rather than
        as an attribute scattered through six modules."""
        for entity in self.made:
            if _on(entity):
                continue
            key = getattr(getattr(entity, "entity_description", None), "key",
                          None)
            if key is None:
                continue
            with self.subTest(entity=entity.unique_id):
                self.assertIn(key, const.OFF_BY_DEFAULT_READINGS)

    def test_every_name_on_the_list_belongs_to_a_real_reading(self):
        """A name nothing uses switches nothing off and looks like it does.

        This integration has already shipped an allow-list with six wrong
        names in it, where every affected field came out null and nothing
        failed -- the file simply said nothing.
        """
        real = {getattr(getattr(entity, "entity_description", None), "key", None)
                for entity in self.made}
        self.assertEqual(const.OFF_BY_DEFAULT_READINGS - real, set())

    def test_every_code_on_the_detection_list_is_one_the_hub_names(self):
        self.assertEqual(
            const.OFF_BY_DEFAULT_DETECTIONS - set(const.DETECTION_NAMES),
            set())

    def test_the_detections_that_are_off_are_the_niche_four(self):
        off = {entity.unique_id.split("_detected_", 1)[1]
               for entity in self.made
               if "_detected_" in entity.unique_id and not _on(entity)}
        self.assertEqual(off, {"vehicle", "pet", "missed_doorbell", "theft"})

    def test_motion_person_and_the_doorbell_are_not_among_them(self):
        for name in ("motion", "person", "doorbell"):
            with self.subTest(detection=name):
                entity = self.by_slug(name)
                self.assertTrue(_on(entity))

    def by_slug(self, slug):
        return next(entity for entity in self.made
                    if entity.unique_id == f"cam0_detected_{slug}")


if __name__ == "__main__":
    unittest.main()
