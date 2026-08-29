"""Entities, constructed and asked -- not read as text.

Fifteen component modules had no test that imported them, so what stood in
for coverage was reading the source and asserting on the string. Two defects
walked straight through that: a storage warning whose branch could never run,
and the silence watchdog below, which switched itself off partway through the
outage it existed to report. Both sat behind a green suite.

These construct the real entity against the real coordinator and call the
real property. `assertIn("last_activity", source)` cannot tell a working
watchdog from a broken one; `self.assertTrue(sensor.is_on)` can.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

binary_sensor = importlib.import_module("tapo_h500.binary_sensor")
const = importlib.import_module("tapo_h500.const")

CAMERA = {"device_id": "cam0", "alias": "Front"}
NOW = 1_786_600_000


def clip(start, length=15):
    return {"startTime": start, "endTime": start + length}


def build_sensor(clips, **options):
    coord, client = harness._build(**options) if options else harness._build()
    coord.clips_for = lambda index: list(clips)
    coord._primed = True
    return binary_sensor.H500CameraSilent(coord, 0, CAMERA), coord


class TheSilenceWatchdog(unittest.TestCase):
    def test_it_is_unknown_before_the_first_poll(self):
        """None reads as "unknown" in the frontend, which is the truth.
        False would say "fine" about a camera nobody has asked about yet."""
        sensor, coord = build_sensor([])
        coord._primed = False
        self.assertIsNone(sensor.is_on)

    def test_a_camera_recording_normally_is_not_flagged(self):
        sensor, _ = build_sensor([clip(NOW - 60)])
        self.assertFalse(sensor.is_on)

    def test_a_camera_past_the_ceiling_is_flagged(self):
        """Nothing at all inside the poll window: silent_seconds reports the
        whole window, which meets the default 24-hour threshold exactly."""
        sensor, _ = build_sensor([])
        self.assertTrue(sensor.is_on)

    def test_the_alarm_does_not_switch_itself_off(self):
        """The defect this file exists for.

        The adaptive half of the test draws its baseline from the clips still
        inside the poll window, and those age out while the camera stays dark,
        so a doorbell that trips at nine hours reads healthy again at twelve.
        The latch is what stops that, and until the entity could be built at
        all, nothing here could tell whether it worked.
        """
        sensor, coord = build_sensor([])
        self.assertTrue(sensor.is_on)          # trips on the ceiling
        # Now let the evidence decay away underneath it: no clips at all means
        # the expectation is zero and the ceiling is all that is left.
        coord.clips_for = lambda index: []
        self.assertTrue(sensor.is_on, "a held alarm must not clear itself")
        self.assertTrue(coord.silent_latched(0))

    def test_recording_again_clears_it(self):
        sensor, coord = build_sensor([])
        self.assertTrue(sensor.is_on)
        coord.clips_for = lambda index: [clip(NOW - 30)]
        self.assertFalse(sensor.is_on)
        self.assertFalse(coord.silent_latched(0))

    def test_the_attributes_say_why(self):
        sensor, _ = build_sensor([])
        attrs = sensor.extra_state_attributes
        self.assertEqual(attrs["silent_seconds"], const.LOOKBACK_SECONDS)
        self.assertIn("expected_events", attrs)
        self.assertIn("held_since_last_recording", attrs)


class TheEntityIsWiredToItsDevice(unittest.TestCase):
    def test_it_carries_a_stable_unique_id(self):
        """Derived from the hub's device_id, not the alias: renaming a camera
        in the app must not orphan its history."""
        sensor, _ = build_sensor([])
        self.assertEqual(sensor.unique_id, "cam0_silent")

    def test_it_is_a_diagnostic_problem_sensor(self):
        sensor, _ = build_sensor([])
        self.assertEqual(sensor.translation_key, "camera_silent")


if __name__ == "__main__":
    unittest.main()
