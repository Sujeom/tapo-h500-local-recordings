"""The diagnostics download, generated and read back.

test_diagnostics_shape.py covers the shape describer; the assembly itself --
what actually lands in a bug report -- ran only at 36%. These build a hub and
generate the file, then hold the two promises that matter: everything a
report needs is present, and nothing that identifies the installation is.
"""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

diagnostics = importlib.import_module("tapo_h500.diagnostics")
dt_util = sys.modules["homeassistant.util.dt"]
NOW = int(dt_util.utcnow().timestamp())


def clip(start, mask=(1 << 1) | (1 << 5), face=None):
    made = {"startTime": start, "endTime": start + 15, "events_1": mask}
    if face:
        made["event_info"] = [{"face_id": face}]
    return made


class TheDownload(unittest.TestCase):
    def setUp(self):
        # The file stamps ages from the wall clock; freeze it to the harness's
        # frozen now so "the newest clip is 300s old" is exact.
        self.addCleanup(setattr, diagnostics, "time", diagnostics.time)
        diagnostics.time = types.SimpleNamespace(time=lambda: NOW)

        self.coord, self.client = harness._build()
        self.coord.cameras = [
            {"device_id": "cam0", "alias": "Front Doorbell", "mac": "AA:BB",
             "device_model": "TD21", "battery_percent": 80,
             "hub_storage_enabled": True},
            {"device_id": "cam1", "alias": "Back Gate", "mac": "CC:DD",
             "device_model": "TD21"},
        ]
        self.coord.data = {"clips": {
            0: [clip(NOW - 300, face=272465657857), clip(NOW - 7200)],
            1: [],
        }}
        self.coord.readings = {"storage_used_percent": 41.5,
                               "storage_total_gb": 32.0, "led_on": True,
                               "cloud_username": "leak@example.com"}
        self.coord.raw_status = {"getLedStatus": {"led": {"config": {
            "enabled": "on"}}}}
        self.client.info = {"device_model": "H500", "sw_version": "1.3.20",
                            "hw_version": "1.0", "mac": "EE:FF",
                            "dev_id": "SECRET", "device_alias": "Our house"}
        self.coord.client = self.client
        self.coord.entry.options = {"poll_interval": 2,
                                    "face_names": {"7": "Sam", "8": "Alex"}}
        self.hass = harness._Hass()
        self.hass.data = {"tapo_h500": {"hubs": {"test": self.coord}}}

    def _download(self):
        return asyncio.run(diagnostics.async_get_config_entry_diagnostics(
            self.hass, self.coord.entry))

    def test_what_a_report_needs_is_there(self):
        report = self._download()
        self.assertEqual(report["device"]["sw_version"], "1.3.20")
        self.assertEqual(report["hub"]["storage_used_percent"], 41.5)
        self.assertEqual(report["coordinator"]["cameras_found"], 2)
        self.assertIn("getLedStatus.led.config.enabled",
                      report["hub_answer_shape"])
        self.assertEqual(report["coordinator"]["wedge_log"], [])

    def test_cameras_are_positions_with_counts_never_names(self):
        cameras = self._download()["cameras"]
        self.assertEqual([c["index"] for c in cameras], [0, 1])
        self.assertEqual(cameras[0]["recordings_in_window"], 2)
        # A face id alone is NOT a face detection: the counts follow the
        # decoded codes, and neither clip carries code 20's bit. Absent means
        # absent, never inferred -- the same discipline as everywhere else.
        self.assertEqual(cameras[0]["detections_by_type"],
                         {"motion": 2, "person": 2})
        text = str(cameras)
        for owners_words in ("Front Doorbell", "Back Gate"):
            self.assertNotIn(owners_words, text)

    def test_ages_are_relative_never_wall_clock(self):
        """"The newest clip is 300s old" answers the same question as a
        timestamp without dating the household."""
        cameras = self._download()["cameras"]
        self.assertEqual(cameras[0]["newest_recording_age"], 300)
        self.assertIsNone(cameras[1]["newest_recording_age"])

    def test_names_are_counted_never_listed(self):
        report = self._download()
        self.assertEqual(report["options"]["named_faces"], 2)
        text = str(report)
        self.assertNotIn("Sam", text)
        self.assertNotIn("Alex", text)

    def test_nothing_identifying_leaves(self):
        """Allow-list redaction: the hub's own identifiers, the cloud
        account, and every MAC stay out even though the sources carry them."""
        text = str(self._download())
        for secret in ("AA:BB", "CC:DD", "EE:FF", "SECRET", "Our house",
                       "leak@example.com"):
            self.assertNotIn(secret, text)

    def test_no_credential_reaches_the_file(self):
        """This is what gets pasted into a public bug report. The entry holds
        the hub's address and three passwords; none of them may appear, and
        checking the assembled file rather than the source is the only way to
        know a future field did not quietly carry one along."""
        self.coord.entry.data = {
            "host": "192.168.11.5", "username": "admin",
            "password": "camera-secret", "cloud_password": "cloud-secret",
        }
        text = str(self._download())
        for secret in ("192.168.11.5", "camera-secret", "cloud-secret"):
            self.assertNotIn(secret, text, secret)

    def test_every_allow_listed_reading_is_one_the_parser_produces(self):
        """An allow-list of names nothing produces is a file full of nulls.

        Six of the sixteen were wrong once -- storage_total for
        storage_total_gb, led_enabled for led_on -- so all three storage
        figures, the LED state, face detection and the audio slots came out
        null in every diagnostics download ever taken. Nothing failed; the
        file simply said nothing, which is the failure an allow-list is most
        prone to.
        """
        status = importlib.import_module("tapo_h500.status")
        produced = set(status.hub_readings({}))
        self.assertEqual(set(diagnostics.SAFE_READINGS) - produced, set())

    def test_a_reading_the_parser_knows_but_the_hub_omitted_is_null(self):
        """Present-as-null, not absent: a missing key in the file reads as
        "this build does not collect that", which sends a reader down the
        wrong road."""
        report = self._download()
        self.assertIn("clock_offset", report["hub"])
        self.assertIsNone(report["hub"]["clock_offset"])

    def test_the_wedge_log_rides_along_once_there_is_one(self):
        self.coord.media.note_status("wedged")
        self.coord.note_recovery_attempt("hub restart")
        log = self._download()["coordinator"]["wedge_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["tried"][0]["what"], "hub restart")


if __name__ == "__main__":
    unittest.main()
