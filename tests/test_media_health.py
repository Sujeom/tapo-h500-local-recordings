"""The wedge is detected before anyone notices their photos are missing.

The hub's known failure: hours after a reboot, port 8800 starts accepting a
TCP connection and closing it before sending a single HTTP byte. Every
download and preview then fails, and the first sign anyone gets is a
notification without a photograph.

The check costs nothing that matters: the first request of a media session
is unauthenticated by design -- the digest challenge is the REPLY to it --
so one small TCP exchange distinguishes a healthy media daemon from a
wedged one with no login, no session and no lockout risk. Verified against
the real hub on 2026-08-17: healthy answers 401 with a digest challenge;
wedged closes with zero bytes.

The classifier is tested against real sockets, because the whole function
is socket behaviour.
"""
import importlib
import json
import socket
import sys
import threading
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
REPAIRS = (COMPONENT / "repairs.py").read_text()
COORDINATOR = (COMPONENT / "coordinator.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

api = importlib.import_module("tapo_h500.api")
const = importlib.import_module("tapo_h500.const")
coordinator_mod = importlib.import_module("tapo_h500.coordinator")


def _serve(handler):
    """One-shot local TCP server; returns its port."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def run():
        try:
            connection, _ = server.accept()
            handler(connection)
        finally:
            server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port


class Classifier(unittest.TestCase):
    def test_a_digest_challenge_is_healthy(self):
        received = {}

        def answer(connection):
            received["request"] = connection.recv(2048)
            connection.sendall(
                b"HTTP/1.1 401 Unauthorized\r\n"
                b'WWW-Authenticate: Digest realm="TP-Link IP-Camera"\r\n\r\n')
            connection.close()

        port = _serve(answer)
        self.assertEqual(
            api.check_media_port("127.0.0.1", port=port, timeout=2), "healthy")
        # The framing the hub was verified to accept: the unauthenticated
        # first POST, zero content length. Anything else tests some other
        # conversation.
        self.assertIn(b"POST /stream", received["request"])
        self.assertIn(b"Content-Length: 0", received["request"])

    def test_a_zero_byte_close_is_the_wedge(self):
        """The signature exactly as observed on the hub: the connection is
        accepted, the request is read, and the socket closes cleanly without
        one byte of reply -- recv returns empty rather than raising."""
        def swallow(connection):
            connection.recv(2048)
            connection.close()

        port = _serve(swallow)
        self.assertEqual(
            api.check_media_port("127.0.0.1", port=port, timeout=2), "wedged")

    def test_a_reset_on_the_request_is_also_the_wedge(self):
        """The harsher variant: closed before the request is even read, so
        the send comes back as a reset. Same daemon, same meaning."""
        port = _serve(lambda connection: connection.close())
        self.assertEqual(
            api.check_media_port("127.0.0.1", port=port, timeout=2), "wedged")

    def test_an_open_but_mute_socket_is_a_stall_not_a_wedge(self):
        def hold(connection):
            try:
                connection.recv(2048)
                threading.Event().wait(3)
            finally:
                connection.close()

        port = _serve(hold)
        self.assertEqual(
            api.check_media_port("127.0.0.1", port=port, timeout=0.3),
            "silent")

    def test_a_refused_connection_is_unreachable(self):
        spare = socket.socket()
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
        spare.close()  # nothing listens here now
        self.assertEqual(
            api.check_media_port("127.0.0.1", port=port, timeout=1),
            "unreachable")

    def test_any_http_bytes_count_as_alive(self):
        """A future firmware answering 400 is still a media daemon talking."""
        def answer(connection):
            connection.recv(2048)
            connection.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            connection.close()

        port = _serve(answer)
        self.assertEqual(
            api.check_media_port("127.0.0.1", port=port, timeout=2), "healthy")


class Cadence(unittest.TestCase):
    """Every fifteen minutes, not every poll, and never mid-session."""

    def _build(self, interval):
        coord, client = harness._build(interval)
        client.checks = 0

        def check():
            client.checks += 1
            return "healthy"

        client.check_media = check
        return coord, client

    def _poll(self, coord):
        import asyncio
        asyncio.run(coord._async_update_data())

    def test_it_runs_on_a_cadence_not_per_poll(self):
        # 450s polls against a 900s cadence: every second poll.
        coord, client = self._build(450)
        for _ in range(6):
            self._poll(coord)
        self.assertEqual(client.checks, 3)

    def test_the_result_is_kept_where_repairs_can_see_it(self):
        coord, client = self._build(450)
        self._poll(coord)
        self.assertEqual(coord.media_status, "healthy")

    def test_it_stays_away_while_a_media_session_is_open(self):
        """An extra connection against a hub mid-download is a variable the
        wedge investigation does not need."""
        coord, client = self._build(450)

        class _Held:
            def locked(self):
                return True

        client._lock = _Held()
        for _ in range(4):
            self._poll(coord)
        self.assertEqual(client.checks, 0)

    def test_a_check_that_blows_up_does_not_fail_the_poll(self):
        coord, client = self._build(450)

        def boom():
            raise OSError("network gone")

        client.check_media = boom
        self._poll(coord)  # must not raise
        self.assertIsNone(coord.media_status)


class Issue(unittest.TestCase):
    def test_it_is_checked_with_the_others(self):
        self.assertIn("_media(hass, entry_id, coordinator)", REPAIRS)

    def test_it_clears_itself(self):
        body = REPAIRS.split("def _media", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("async_delete_issue", body)

    def test_it_is_an_error_and_only_for_the_wedge(self):
        """Unreachable already has its own issue, and "silent" is one slow
        reply away from crying wolf; only the proven signature alarms."""
        body = REPAIRS.split("def _media", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('== "wedged"', body)
        self.assertIn("IssueSeverity.ERROR", body)

    def test_it_has_a_title_and_says_what_to_do(self):
        issue = STRINGS["issues"]["media_wedged"]
        self.assertTrue(issue["title"])
        self.assertIn("reboot", issue["description"].lower())

    def test_recordings_and_notifications_are_both_named(self):
        """The two things the owner will actually see broken."""
        text = STRINGS["issues"]["media_wedged"]["description"].lower()
        self.assertIn("recording", text)
        self.assertIn("photo", text)


if __name__ == "__main__":
    unittest.main()


class StandaloneTool(unittest.TestCase):
    """tools/check-media.py sends the same conversation the tested
    classifier does, so its verdicts mean the same thing."""

    TOOL = (Path(__file__).parents[1] / "tools" / "check-media.py").read_text()

    def test_it_speaks_the_verified_framing(self):
        self.assertIn("POST /stream HTTP/1.1", self.TOOL)
        self.assertIn("Content-Length: 0", self.TOOL)

    def test_it_never_logs_in(self):
        for forbidden in ("password", "TAPO_", "pytapo", "Digest response"):
            self.assertNotIn(forbidden, self.TOOL)

    def test_it_names_all_four_verdicts(self):
        for verdict in ("healthy", "wedged", "unreachable", "silent"):
            self.assertIn(f'"{verdict}"', self.TOOL)


BINARY_SENSOR = (COMPONENT / "binary_sensor.py").read_text()
DIAGNOSTICS = (COMPONENT / "diagnostics.py").read_text()


class MediaProblemSensor(unittest.TestCase):
    """The wedge as an entity, so it can trigger an automation.

    The repair notice tells a human; a binary sensor lets an automation act
    -- notify the phone, power-cycle a smart plug. Problem class: on means
    wedged, off means the handshake answered, unknown before the first
    check.
    """

    BODY = BINARY_SENSOR.split("class H500MediaProblem", 1)[1].split(
        "\nclass ", 1)[0]

    def test_it_reads_the_sentinels_verdict(self):
        self.assertIn("media_status", self.BODY)
        self.assertIn('== "wedged"', self.BODY)

    def test_unknown_before_the_first_check(self):
        self.assertIn("return None", self.BODY)

    def test_the_raw_verdict_is_an_attribute(self):
        """"unreachable" and "silent" do not alarm, but an automation may
        still want them."""
        self.assertIn('"media_status"', self.BODY)

    def test_it_is_registered(self):
        self.assertIn("H500MediaProblem(coordinator, entry)", BINARY_SENSOR)


class DiagnosticsCarryTheEvidence(unittest.TestCase):
    """A wedge bug report needs the numbers the investigation runs on."""

    def test_session_count_status_and_failures_are_included(self):
        for key in ("media_status", "media_sessions", "download_failures"):
            self.assertIn(f'"{key}"', DIAGNOSTICS)

    def test_failures_are_keyed_by_index_not_by_name(self):
        """Camera aliases are the owner's own words and never leave in a
        diagnostics file."""
        body = DIAGNOSTICS.split('"download_failures"', 1)[1][:200]
        self.assertIn("_download_failures", body)
        self.assertNotIn("alias", body)
