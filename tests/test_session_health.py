"""Whether the media path is working, without grepping a log.

Every download and every preview is a whole session of its own. How they were
going was visible only in the debug log, which requires debug logging to have
been turned on before the trouble started -- so the answer was unavailable
exactly when somebody wanted it.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

api = importlib.import_module("tapo_h500.api")
const = importlib.import_module("tapo_h500.const")
sensor_mod = importlib.import_module("tapo_h500.sensor")


class Outcomes(unittest.TestCase):
    def test_bytes_that_arrived_cleanly_are_served(self):
        self.assertEqual(api.session_outcome("closed", 4096), "served")

    def test_a_clean_session_carrying_nothing_is_empty(self):
        """The failure this hub actually has, and the one a log line hides
        best: it looks like a success everywhere except the byte count."""
        self.assertEqual(api.session_outcome("closed", 0), "empty")

    def test_anything_that_raised_is_a_failure(self):
        for ended in ("IncompleteRecordingError", "TimeoutError",
                      "CancelledError", "GeneratorExit"):
            with self.subTest(ended):
                self.assertEqual(api.session_outcome(ended, 0), "failed")

    def test_a_session_that_raised_after_bytes_is_still_a_failure(self):
        """A truncated recording is not a served one. The caller cannot use
        what it got."""
        self.assertEqual(api.session_outcome("IncompleteRecordingError", 900),
                         "failed")


class _Client:
    """Just the counters, which is the whole surface the sensor reads."""

    def __init__(self, outcomes=(), sessions=None):
        # The client's own ring, not a lookalike: its bound is under test.
        self._session_log = api.new_session_log()
        self._session_log.extend(outcomes)
        self._sessions = len(outcomes) if sessions is None else sessions

    session_health = api.H500Client.session_health
    info = {"device_model": "H500"}


class TheRing(unittest.TestCase):
    def test_it_counts_the_three_outcomes(self):
        health = _Client(["served", "served", "empty", "failed"]).session_health
        self.assertEqual(
            (health["served"], health["empty"], health["failed"]), (2, 1, 1))
        self.assertEqual(health["recent"], 4)

    def test_it_forgets_beyond_the_window(self):
        """A hub that wedged this morning and has served everything since
        must not read as half broken for the rest of the week."""
        client = _Client(["empty"] * const.SESSION_HISTORY)
        for _ in range(const.SESSION_HISTORY):
            client._session_log.append("served")
        health = client.session_health
        self.assertEqual(health["empty"], 0)
        self.assertEqual(health["served"], const.SESSION_HISTORY)

    def test_the_total_is_not_the_window(self):
        """"It wedges after about N sessions" needs N, which the ring has
        long since dropped."""
        client = _Client(["served"] * 10, sessions=4321)
        self.assertEqual(client.session_health["sessions"], 4321)
        self.assertEqual(client.session_health["recent"], 10)


class TheSensor(unittest.TestCase):
    def _sensor(self, outcomes=(), sessions=None):
        coord, _ = harness._build()
        coord.client = _Client(outcomes, sessions)
        return sensor_mod.H500MediaSessions(coord, harness._Entry(20))

    def test_the_state_is_the_running_count(self):
        self.assertEqual(self._sensor(["served"], sessions=812).native_value,
                         812)

    def test_the_recorder_graphs_it(self):
        """"It wedges after about N sessions" is worth graphing, and a
        running count is what the recorder turns into that."""
        self.assertIs(sensor_mod.H500MediaSessions._attr_state_class,
                      sensor_mod.SensorStateClass.TOTAL_INCREASING)

    def test_the_breakdown_is_beside_it(self):
        attributes = self._sensor(
            ["served"] * 8 + ["empty", "failed"]).extra_state_attributes
        self.assertEqual(attributes["served"], 8)
        self.assertEqual(attributes["empty"], 1)
        self.assertEqual(attributes["failed"], 1)
        self.assertEqual(attributes["failure_percent"], 20.0)

    def test_nothing_fetched_yet_is_not_an_all_clear(self):
        """Zero percent on a hub nobody has asked for a recording reads as
        fine. It is not a reading at all."""
        self.assertIsNone(
            self._sensor().extra_state_attributes["failure_percent"])

    def test_a_client_without_the_counters_does_not_take_it_down(self):
        """A test double, or an older process mid-upgrade."""
        coord, client = harness._build()
        sensor = sensor_mod.H500MediaSessions(coord, harness._Entry(20))
        self.assertIsNone(sensor.native_value)
        self.assertIsNone(
            sensor.extra_state_attributes["failure_percent"])

    def test_it_is_registered_and_named(self):
        self.assertIn("H500MediaSessions(coordinator, entry)",
                      (COMPONENT / "sensor.py").read_text())
        for name in ("translations/en.json", "strings.json"):
            with self.subTest(name):
                doc = json.loads((COMPONENT / name).read_text())
                self.assertIn("media_sessions", doc["entity"]["sensor"])

    def test_every_session_is_recorded_as_it_ends(self):
        """In the `finally`, so an abandoned generator and a stalled stream
        are counted too. Those are the sessions worth counting.
        """
        body = (COMPONENT / "api.py").read_text().split(
            "async def iter_recording", 1)[1]
        after = body.split("finally:", 1)[1]
        self.assertIn("self._session_log.append(session_outcome(ended, "
                      "received))", after)

    def test_diagnostics_carry_the_same_figures(self):
        source = (COMPONENT / "diagnostics.py").read_text()
        self.assertIn('"session_health": getattr(', source)


if __name__ == "__main__":
    unittest.main()
