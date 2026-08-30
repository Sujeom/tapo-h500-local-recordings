"""The logbook rows, phrased by the real code.

Without this module the logbook shows "Front Doorbell Activity changed to
<timestamp>" -- a timestamp printed twice. The phrasing here is what makes
history readable, and it must agree with the cards and the notifications,
which decode the same codes.
"""
import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

logbook = importlib.import_module("tapo_h500.logbook")


def _event(**data):
    return types.SimpleNamespace(data=data)


class ThePhrase(unittest.TestCase):
    def test_a_ring_leads_with_the_doorbell(self):
        self.assertEqual(logbook._phrase([2, 10, 17]),
                         "someone rang the doorbell (motion)")

    def test_a_bare_ring_has_no_empty_brackets(self):
        self.assertEqual(logbook._phrase([17]), "someone rang the doorbell")

    def test_code_10_never_reads_as_a_contradiction_beside_a_press(self):
        """"someone rang the doorbell (missed doorbell)" is nonsense, and 10
        rides along with every press."""
        phrased = logbook._phrase([10, 17])
        self.assertNotIn("missed", phrased)

    def test_a_missed_press_alone_still_says_so(self):
        self.assertEqual(logbook._phrase([10]), "missed doorbell")

    def test_plain_detections_read_as_a_list(self):
        self.assertEqual(logbook._phrase([2, 6]), "motion, person")

    def test_an_unnamed_code_shows_its_number_rather_than_a_guess(self):
        self.assertEqual(logbook._phrase([2, 31]), "motion, type 31")

    def test_nothing_decoded_still_says_something(self):
        self.assertEqual(logbook._phrase([]), "activity")


class TheDescribers(unittest.TestCase):
    def _register(self):
        described = {}

        def register(domain, event_type, describe):
            described[event_type] = describe

        logbook.async_describe_events(harness._Hass(), register)
        return described

    def test_both_events_get_a_describer(self):
        described = self._register()
        self.assertEqual(set(described),
                         {"tapo_h500_event", "tapo_h500_arrival"})

    def test_an_activity_row_names_the_camera_and_what_happened(self):
        describe = self._register()["tapo_h500_event"]
        row = describe(_event(name="Front Doorbell",
                              detection_types=[2, 6]))
        self.assertEqual(row, {"name": "Front Doorbell",
                               "message": "motion, person"})

    def test_a_bare_event_still_renders(self):
        describe = self._register()["tapo_h500_event"]
        self.assertEqual(describe(_event()),
                         {"name": "Camera", "message": "activity"})

    def test_an_arrival_says_who_and_where(self):
        describe = self._register()["tapo_h500_arrival"]
        self.assertEqual(
            describe(_event(name="Alice", camera="Front")),
            {"name": "Alice", "message": "arrived, first seen at Front"})

    def test_an_arrival_with_no_camera_does_not_dangle(self):
        describe = self._register()["tapo_h500_arrival"]
        row = describe(_event(name="Alice"))
        self.assertEqual(row["message"], "arrived")

    def test_an_unnamed_arrival_is_still_somebody(self):
        describe = self._register()["tapo_h500_arrival"]
        self.assertEqual(describe(_event(camera="Front"))["name"], "Someone")


if __name__ == "__main__":
    unittest.main()
