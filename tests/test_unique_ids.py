"""The identifiers every automation and dashboard in the world already names.

An entity's unique id is what the registry keys on, and the entity id people
write in their automations is built from it. Change one and every automation,
every dashboard card and every history graph that named that entity stops
working -- silently, because Home Assistant simply registers a new entity
beside the orphaned old one.

So the list below is frozen on purpose. Changing it is a deliberate act with
a migration attached, never a side effect of tidying a translation key up.
The README used to say entity names could change without a migration; this is
what replaced that sentence.

Two cameras and two named people, which is the shape that produces one of
everything -- per hub, per camera, and per person.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

component = __import__("ha_stubs").real_module("init")

PLATFORMS = ("binary_sensor", "button", "calendar", "camera", "event", "image",
             "number", "select", "sensor", "siren", "switch", "update")

FROZEN = [
    "cam0_activity",
    "cam0_activity_level",
    "cam0_ai_enhance",
    "cam0_ai_enhance_enabled",
    "cam0_busiest_hour",
    "cam0_calendar",
    "cam0_camera",
    "cam0_contact_sheet",
    "cam0_continuous_recording",
    "cam0_detected_doorbell",
    "cam0_detected_face",
    "cam0_detected_missed_doorbell",
    "cam0_detected_motion",
    "cam0_detected_person",
    "cam0_detected_pet",
    "cam0_detected_theft",
    "cam0_detected_unknown_face",
    "cam0_detected_vehicle",
    "cam0_hub_storage",
    "cam0_last_activity",
    "cam0_latest_event",
    "cam0_loitering",
    "cam0_model",
    "cam0_network_mode",
    "cam0_people_seen",
    "cam0_possible_delivery",
    "cam0_recordings_1h",
    "cam0_recordings_24h",
    "cam0_recordings_today",
    "cam0_silent",
    "cam0_unknown_faces",
    "cam0_unusual_activity",
    "cam0_visits_24h",
    "cam0_wifi_backup",
    "cam1_activity",
    "cam1_activity_level",
    "cam1_ai_enhance",
    "cam1_ai_enhance_enabled",
    "cam1_busiest_hour",
    "cam1_calendar",
    "cam1_camera",
    "cam1_contact_sheet",
    "cam1_continuous_recording",
    "cam1_detected_doorbell",
    "cam1_detected_face",
    "cam1_detected_missed_doorbell",
    "cam1_detected_motion",
    "cam1_detected_person",
    "cam1_detected_pet",
    "cam1_detected_theft",
    "cam1_detected_unknown_face",
    "cam1_detected_vehicle",
    "cam1_hub_storage",
    "cam1_last_activity",
    "cam1_latest_event",
    "cam1_loitering",
    "cam1_model",
    "cam1_network_mode",
    "cam1_people_seen",
    "cam1_possible_delivery",
    "cam1_recordings_1h",
    "cam1_recordings_24h",
    "cam1_recordings_today",
    "cam1_silent",
    "cam1_unknown_faces",
    "cam1_unusual_activity",
    "cam1_visits_24h",
    "cam1_wifi_backup",
    "test_auto_upgrade",
    "test_auto_upgrade_time",
    "test_cameras_dark",
    "test_clock_offset",
    "test_custom_sounds",
    "test_diagnose_mode",
    "test_face_7",
    "test_face_7_location",
    "test_face_7_recent",
    "test_face_8",
    "test_face_8_location",
    "test_face_8_recent",
    "test_face_detection",
    "test_firmware",
    "test_firmware_state",
    "test_hub_health",
    "test_ip_address",
    "test_led",
    "test_loop_recording",
    "test_media_encrypted",
    "test_media_healthy_for",
    "test_media_problem",
    "test_media_sessions",
    "test_people_seen_recently",
    "test_prowling",
    "test_restart",
    "test_scheduled_reboot",
    "test_siren",
    "test_siren_duration",
    "test_siren_time_left",
    "test_siren_tone",
    "test_siren_volume",
    "test_snoozed",
    "test_storage_free",
    "test_storage_full_in",
    "test_storage_problem",
    "test_storage_status",
    "test_storage_total",
    "test_storage_used",
    "test_timezone",
]


def _built():
    coord, client = harness._build()
    coord.cameras = [
        {"device_id": "cam0", "alias": "Front", "device_model": "TD21"},
        {"device_id": "cam1", "alias": "Side", "device_model": "TD21"},
    ]
    client.siren_tones = lambda: ["Doorbell"]
    coord.client = client
    coord.entry.options = {**coord.entry.options,
                           "face_names": {"7": "Sam", "8": "Alex"}}
    coord.entry.async_on_unload = lambda unsub: None
    hass = harness._hass_with(coord)
    made = []
    for name in PLATFORMS:
        module = importlib.import_module(f"tapo_h500.{name}")
        asyncio.run(module.async_setup_entry(hass, coord.entry, made.extend))
    return made


class TheUniqueIdsAreFrozen(unittest.TestCase):
    def setUp(self):
        self.made = _built()

    def test_the_whole_set_is_exactly_what_was_frozen(self):
        """If this fails, read the diff before touching it. An id that
        appeared is fine; an id that changed or vanished orphans whatever
        named it, and needs a registry migration rather than an edit here.
        """
        self.assertEqual(sorted(entity.unique_id for entity in self.made),
                         FROZEN)

    def test_every_platform_is_represented(self):
        """A freeze that silently omits a platform protects nothing there."""
        prefixes = {"cam0", "cam1", "test"}
        for unique_id in FROZEN:
            with self.subTest(unique_id=unique_id):
                self.assertTrue(
                    any(unique_id.startswith(p) for p in prefixes), unique_id)
        self.assertEqual(len(self.made), len(FROZEN))

    def test_none_of_them_collide(self):
        """Two entities sharing an id means Home Assistant drops one, and
        which one depends on setup order."""
        seen = [entity.unique_id for entity in self.made]
        self.assertEqual(len(seen), len(set(seen)))

    def test_a_camera_id_is_the_hubs_own_device_id(self):
        """Not the index. An index shifts when a camera is unpaired, and
        every entity for every other camera would be renamed."""
        self.assertTrue(any(i.startswith("cam0_") for i in FROZEN))
        self.assertFalse(any(i.startswith("0_") for i in FROZEN))


class TheEntryCanBeMigrated(unittest.TestCase):
    """The hook exists before there is anything to migrate.

    Face names live on this entry and cost months to rebuild, so the first
    migration should be a small change to a function that already exists
    rather than a new one written under pressure.
    """

    def _entry(self, version=1, minor=1):
        entry = harness._Entry(20)
        entry.version = version
        entry.minor_version = minor
        return entry

    def test_the_current_version_needs_nothing_doing(self):
        self.assertTrue(asyncio.run(
            component.async_migrate_entry(harness._Hass(), self._entry())))

    def test_an_entry_from_the_future_is_refused(self):
        """Downgrading Home Assistant leaves an entry this version cannot
        read. Refusing says so; pretending to succeed loses the names."""
        self.assertFalse(asyncio.run(
            component.async_migrate_entry(harness._Hass(),
                                          self._entry(version=2))))

    def test_the_flow_and_the_migration_agree_about_the_version(self):
        """A flow creating entries at a version the migration does not know
        is the shape of the bug this hook exists to prevent."""
        config_flow = importlib.import_module("tapo_h500.config_flow")
        self.assertEqual(config_flow.TapoH500ConfigFlow.VERSION, 1)


if __name__ == "__main__":
    unittest.main()
