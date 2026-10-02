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

    def test_the_camera_record_is_described_never_quoted(self):
        """Names and types of every field the hub put on the paired record,
        the way the hub status is already described. A dual-lens camera may
        carry a field no TD21 has, and the allow-list cannot name what
        nobody has seen."""
        cameras = self._download()["cameras"]
        self.assertEqual(cameras[0]["record_shape"]["alias"], "str")
        self.assertEqual(cameras[0]["record_shape"]["battery_percent"], "int")
        self.assertNotIn("record_shape", str(cameras[0]["record_shape"]))
        self.assertNotIn("Front Doorbell", str(cameras))

    def test_the_second_lens_is_asked_about(self):
        """Every search the integration makes asks for lens 0, which is every
        lens a TD21 has. A dual-lens camera records two, and nobody has seen
        which channel the second one answers on: the file asks the hub for
        channel 1 over the same window the poll uses and reports what came
        back -- a count and the shape of one clip, never the clips."""
        asked = []

        def recent(camera, start, end, channel=0):
            asked.append((camera["device_id"], channel, end - start))
            return [clip(NOW - 100)] if camera["device_id"] == "cam0" else []

        self.client.recent = recent
        cameras = self._download()["cameras"]
        self.assertEqual(asked, [("cam0", 1, diagnostics.LOOKBACK_SECONDS + 60),
                                 ("cam1", 1, diagnostics.LOOKBACK_SECONDS + 60)])
        self.assertEqual(cameras[0]["second_lens"], {
            "recordings": 1,
            "shape": {"startTime": "int", "endTime": "int", "events_1": "int"}})
        self.assertEqual(cameras[1]["second_lens"],
                         {"recordings": 0, "shape": {}})
        self.assertNotIn(str(NOW - 100), str(cameras))

    def test_a_refused_second_lens_reports_the_code_never_the_body(self):
        """A hub that has no lens 1 says so with a code, and the code is the
        answer. The body pytapo quotes is the hub's own reply and stays out,
        like every other hub value; and a probe never fails the download."""
        def recent(camera, start, end, channel=0):
            raise RuntimeError(
                'Error: refused, Response: {"error_code": -40209, '
                '"note": "Our house"}')

        self.client.recent = recent
        report = self._download()
        self.assertEqual(report["cameras"][0]["second_lens"],
                         {"error_code": -40209})
        self.assertNotIn("Our house", str(report))

    def test_no_secret_from_any_source_reaches_the_file(self):
        """Every object the download reads, loaded with something that must
        not leave: the entry's credentials, the client's own copies of them
        and its session material, the hub's session token, a camera record
        carrying a backup Wi-Fi password, a status reply carrying a token.
        The whole file is serialised and searched, because an allow-list is
        only as good as the last field somebody added to it."""
        import json
        self.coord.entry.data = {
            "host": "192.168.11.5", "username": "admin",
            "password": "camera-secret", "cloud_password": "cloud-secret",
        }
        self.client.host = "192.168.11.5"
        self.client.password = "camera-secret"
        self.client.cloud_password = "cloud-secret"
        self.client._super_secret_key = "super-secret-key"
        self.client.player_id = "player-uuid-1234"
        self.client._hub = types.SimpleNamespace(
            stok="session-token", superSecretKey="super-secret-key",
            password="camera-secret")
        self.coord.cameras[0]["backup_wifi"] = {
            "ssid": "Our House Wifi", "password": "wifi-secret"}
        self.coord.cameras[0]["ip"] = "192.168.11.77"
        self.coord.raw_status = {"getDeviceInfo": {"basic_info": {
            "token": "status-token", "ssid": "Our House Wifi",
            "mac": "EE:FF"}}}
        text = json.dumps(self._download(), default=str)
        for secret in ("192.168.11.5", "192.168.11.77", "admin",
                       "camera-secret", "cloud-secret", "super-secret-key",
                       "player-uuid-1234", "session-token", "status-token",
                       "Our House Wifi", "wifi-secret", "AA:BB", "EE:FF",
                       "Front Doorbell"):
            self.assertNotIn(secret, text, secret)

    def test_the_wedge_log_rides_along_once_there_is_one(self):
        self.coord.media.note_status("wedged")
        self.coord.note_recovery_attempt("hub restart")
        log = self._download()["coordinator"]["wedge_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["tried"][0]["what"], "hub restart")


if __name__ == "__main__":
    unittest.main()
