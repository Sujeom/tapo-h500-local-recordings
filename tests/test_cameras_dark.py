"""Every camera stopping at once is a different fact from one going quiet.

One quiet camera is a quiet back gate, which is why its own sensor is
adjustable and is not an alarm by itself. Every camera quiet at the same
moment, on a hub still answering every poll, is the failure this project
exists around: some hours after the hub restarts the cameras go dark, keep
their radio link, still answer live view, and record nothing. The app shows
them connected.

There is nothing to read that says otherwise -- the hub's paired-device record
has no online flag, no signal strength and no battery. The simultaneity is the
entire signal, so it needs an entity of its own rather than a glance across
several.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

binary_sensor = importlib.import_module("tapo_h500.binary_sensor")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
CAMERAS = [{"device_id": "cam0", "alias": "Front"},
           {"device_id": "cam1", "alias": "Back Gate"}]


def clip(start, length=15):
    return {"startTime": start, "endTime": start + length}


class TheSignal(unittest.TestCase):
    def _sensor(self, per_camera, cameras=CAMERAS, primed=True):
        """`per_camera` maps index -> that camera's clips."""
        coord, _ = harness._build()
        coord.cameras = list(cameras)
        coord._primed = primed
        coord.clips_for = lambda index: list(per_camera.get(index, []))
        return binary_sensor.H500CamerasDark(coord, harness._Entry(20)), coord

    def test_all_of_them_dark_is_the_alarm(self):
        sensor, _ = self._sensor({0: [], 1: []})
        self.assertIs(sensor.is_on, True)

    def test_one_quiet_camera_is_not(self):
        """A back gate that genuinely sees nobody for a day. Its own sensor
        says so; this one must not."""
        sensor, _ = self._sensor({0: [clip(NOW - 60)], 1: []})
        self.assertIs(sensor.is_on, False)

    def test_everything_recording_is_not(self):
        sensor, _ = self._sensor({0: [clip(NOW - 60)], 1: [clip(NOW - 90)]})
        self.assertIs(sensor.is_on, False)

    def test_unknown_before_the_first_poll(self):
        """Every camera looks silent before anything has been asked, and an
        alarm about the integration is not an alarm about the hardware."""
        sensor, _ = self._sensor({0: [], 1: []}, primed=False)
        self.assertIsNone(sensor.is_on)

    def test_unknown_when_no_camera_is_paired(self):
        """Nothing is dark if nothing exists. `all([])` is True, which would
        make an empty hub report the alarm forever."""
        sensor, _ = self._sensor({}, cameras=[])
        self.assertIsNone(sensor.is_on)

    def test_it_asks_the_same_question_the_per_camera_sensor_does(self):
        """Including the adaptive half. A camera well inside the ceiling but
        long past its own expectation is dark for both, or the dashboard
        contradicts itself."""
        busy = [clip(NOW - 4 * 3600 - n * 1800) for n in range(48)]
        sensor, coord = self._sensor({0: busy, 1: busy})
        self.assertLess(coord.silent_seconds(0), 24 * 3600)
        self.assertIs(sensor.is_on, True)
        self.assertTrue(
            binary_sensor.H500CameraSilent(coord, 0, CAMERAS[0]).is_on)

    def test_the_attributes_say_how_long_and_whether_the_hub_went_too(self):
        """Both failing is one hub problem; this alone is the cameras, and
        telling those apart is the point of having it."""
        sensor, coord = self._sensor(
            {0: [clip(NOW - 30 * 3600)], 1: [clip(NOW - 9 * 3600)]})
        coord.media.status = "healthy"
        attributes = sensor.extra_state_attributes
        self.assertEqual(attributes["cameras"], 2)
        self.assertEqual(attributes["media_status"], "healthy")
        self.assertEqual(attributes["dark_for_hours"], 9.0,
                         "when the last one stopped, not the quietest")

    def test_it_is_registered_once_for_the_hub(self):
        source = (COMPONENT / "binary_sensor.py").read_text()
        self.assertIn("H500CamerasDark(coordinator, entry)", source)

    def test_it_is_named(self):
        for name in ("translations/en.json", "strings.json"):
            with self.subTest(name):
                doc = json.loads((COMPONENT / name).read_text())
                self.assertIn("cameras_dark", doc["entity"]["binary_sensor"])


if __name__ == "__main__":
    unittest.main()
