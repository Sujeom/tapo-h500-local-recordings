"""A camera that has stopped working looks exactly like a quiet one.

The hub offers nothing to check: 16 fields in the paired-device record and not
one is an online flag, a signal strength or a battery, and all eleven battery
methods answer -40106. So silence is the only evidence there is, and these
tests are mostly about the two ways of getting that wrong -- calling every
camera silent before the first poll has happened, and claiming to know about
a period longer than the hub was ever asked about.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
BINARY_SENSOR = (COMPONENT / "binary_sensor.py").read_text()
REPAIRS = (COMPONENT / "repairs.py").read_text()
CONFIG_FLOW = (COMPONENT / "config_flow.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000


class _Client:
    def __init__(self):
        self.clips = []

    def cameras(self):
        return [{"device_id": "cam0", "alias": "Front"},
                {"device_id": "cam1", "alias": "Back Gate"}]

    def recent(self, camera, start, end):
        return list(self.clips) if camera["device_id"] == "cam0" else []

    def detections(self, camera, start, end):
        return []

    def hub_status(self):
        return {}


def build(**options):
    client = _Client()
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20, **options), client)
    coord._download_new = lambda *a, **k: None
    return coord, client


def poll(coord):
    coord.data = asyncio.run(coord._async_update_data())


class BeforeTheFirstPoll(unittest.TestCase):
    def test_nothing_is_known_yet(self):
        """An empty list before the hub has been asked means "not asked",
        not "nothing happened". Reporting every camera silent on startup
        would be an alarm about the integration, not the hardware."""
        coord, _ = build()
        self.assertIsNone(coord.silent_seconds(0))

    def test_and_so_no_camera_is_reported(self):
        coord, client = build()
        # The paired list is fetched at the top of the first poll, before any
        # recordings are. That window -- cameras known, activity not -- is
        # where "silent" must still mean "not yet known".
        coord.cameras = client.cameras()
        self.assertEqual(coord.silent_cameras(3600), [])


class AfterAPoll(unittest.TestCase):
    def test_a_camera_with_recent_activity_is_not_silent(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertLess(coord.silent_seconds(0), 3600)

    def test_a_camera_with_nothing_at_all_reports_the_whole_window(self):
        """Not more. The hub was asked about a day; anything longer would be
        invented, and this is the number a threshold is compared against."""
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertEqual(coord.silent_seconds(1), const.LOOKBACK_SECONDS)

    def test_it_names_only_the_quiet_cameras(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 60, "endTime": NOW - 45}]
        poll(coord)
        self.assertEqual(coord.silent_cameras(3600), ["Back Gate"])

    def test_a_higher_threshold_forgives_a_quiet_camera(self):
        coord, client = build()
        client.clips = [{"startTime": NOW - 7200, "endTime": NOW - 7185}]
        poll(coord)
        self.assertEqual(coord.silent_cameras(3600), ["Front", "Back Gate"])
        self.assertEqual(coord.silent_cameras(const.LOOKBACK_SECONDS),
                         ["Back Gate"])

    def test_a_clock_ahead_of_the_hub_does_not_go_negative(self):
        """A recording stamped in the future would otherwise produce a
        negative silence, which reads as "silent for -40 seconds"."""
        coord, client = build()
        client.clips = [{"startTime": NOW + 300, "endTime": NOW + 315}]
        poll(coord)
        self.assertEqual(coord.silent_seconds(0), 0)


class Threshold(unittest.TestCase):
    def test_the_option_cannot_exceed_the_poll_window(self):
        """The form caps it, but an entry saved before the cap existed could
        hold anything, and a threshold past the window makes a sensor that
        never turns on for a reason nobody can see."""
        body = BINARY_SENSOR.split("def silent_threshold", 1)[1].split(
            "\n\nclass ", 1)[0]
        self.assertIn("LOOKBACK_SECONDS", body)
        self.assertIn("min(", body)

    def test_the_form_offers_no_more_than_the_window(self):
        settings = CONFIG_FLOW.split("async_step_settings", 1)[1]
        self.assertIn("LOOKBACK_SECONDS // 3600", settings)

    def test_the_option_is_on_the_settings_form(self):
        settings = CONFIG_FLOW.split("async_step_settings", 1)[1]
        self.assertIn("CONF_SILENT_HOURS", settings)


class Entity(unittest.TestCase):
    def test_every_camera_gets_one(self):
        setup = BINARY_SENSOR.split("async_setup_entry", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn("H500CameraSilent(coordinator, index, camera)", setup)

    def test_it_is_unknown_rather_than_fine_before_the_first_poll(self):
        body = BINARY_SENSOR.split("class H500CameraSilent", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn("if seconds is None:\n            return None", body)


class Repair(unittest.TestCase):
    def test_it_raises_an_issue(self):
        self.assertIn("SILENT_CAMERA_ISSUE", REPAIRS)
        self.assertIn("_silent_cameras(hass, entry_id, coordinator)", REPAIRS)

    def test_the_issue_clears_when_the_camera_comes_back(self):
        """An issue that never clears is worse than none."""
        body = REPAIRS.split("def _silent_cameras", 1)[1].split(
            "\n\ndef ", 1)[0]
        self.assertIn("async_delete_issue", body)


if __name__ == "__main__":
    unittest.main()
