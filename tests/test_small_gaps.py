"""Three small surfaces with sharp edges: the restart button, the fix flow,
and the setup form's one login.

Each is short and each encodes a hardware lesson -- which failure shape means
no, which means yes, and why setup logs in exactly once.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

from homeassistant.exceptions import HomeAssistantError  # noqa: E402

api = importlib.import_module("tapo_h500.api")
button_mod = importlib.import_module("tapo_h500.button")
repairs = importlib.import_module("tapo_h500.repairs")
config_flow = importlib.import_module("tapo_h500.config_flow")


def run(coro):
    return asyncio.run(coro)


class TheRestartButton(unittest.TestCase):
    def _press(self, reboot):
        coord, client = harness._build()
        self.asked = []

        def watched():
            self.asked.append(True)
            return reboot()

        client.reboot = watched
        coord.client = client
        entity = button_mod.H500RestartButton(coord, harness._Entry(20))
        entity.hass = harness._Hass()
        run(entity.async_press())
        return entity

    def test_a_clean_acknowledgement_is_success(self):
        self._press(lambda: {"error_code": 0})
        self.assertEqual(self.asked, [True], "the hub was actually asked")

    def test_the_connection_dying_is_what_success_looks_like(self):
        """The device being asked is the device carrying the answer; the
        drop IS the reboot, exactly as through the nightly restart. Passing
        here means no exception reached the person -- so the call itself is
        checked too, or a button that quietly asked nothing would pass."""
        def drop():
            raise ConnectionResetError("peer went away")

        self._press(drop)
        self.assertEqual(self.asked, [True])

    def test_a_protocol_refusal_reaches_the_person_who_pressed(self):
        def refuse():
            raise OSError("error -40209 from the hub")

        with self.assertRaises(HomeAssistantError) as caught:
            self._press(refuse)
        self.assertIn("refused", str(caught.exception))


class TheFixFlow(unittest.TestCase):
    def _flow(self, coord=None, data=None):
        hass = harness._Hass()
        if coord is not None:
            hass.data = {"tapo_h500": {"hubs": {"test": coord}}}
            hass.config_entries = harness._ConfigEntries(
                [coord.entry])
            coord.entry.runtime_data = coord
        flow = run(repairs.async_create_fix_flow(
            hass, "unnamed_face_test",
            data if data is not None else
            {"entry_id": "test", "face_id": "7", "sightings": "12",
             "cameras": "Front", "others": "1"}))
        flow.hass = hass
        return flow

    def test_the_form_says_who_it_is_asking_about(self):
        """The counts travel through the issue's data, or a fixable issue
        -- which may carry no description -- could not show them at all."""
        form = run(self._flow(harness._build()[0]).async_step_init())
        told = form["description_placeholders"]
        self.assertEqual(told, {"face_id": "7", "sightings": "12",
                                "cameras": "Front", "others": "1"})

    def test_a_name_lands_where_everything_reads_it(self):
        coord, _ = harness._build()
        coord.entry.options = {**coord.entry.options,
                               "face_names": {"9": "Bob"}}
        flow = self._flow(coord)
        result = run(flow.async_step_init({"name": "  Alice "}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(coord.entry.options["face_names"],
                         {"9": "Bob", "7": "Alice"},
                         "merged, stripped, nobody else dropped")

    def test_an_empty_answer_names_nobody_and_just_closes(self):
        coord, _ = harness._build()
        flow = self._flow(coord)
        result = run(flow.async_step_init({"name": "   "}))
        self.assertEqual(result["type"], "create_entry")
        self.assertNotIn("7", coord.entry.options.get("face_names") or {})

    def test_an_unloaded_entry_still_closes_instead_of_crashing(self):
        flow = self._flow(coord=None)
        result = run(flow.async_step_init({"name": "Alice"}))
        self.assertEqual(result["type"], "create_entry")


class _SetupClient:
    outcome = None
    made: list = []

    def __init__(self, host, username, password, cloud):
        self.closes = 0
        type(self).made.append(self)

    def connect(self):
        if isinstance(type(self).outcome, Exception):
            raise type(self).outcome

    def cameras(self):
        return type(self).outcome or []

    def close(self):
        self.closes += 1


class TheOneSetupLogin(unittest.TestCase):
    def _flow(self):
        flow = config_flow.TapoH500ConfigFlow()
        flow.hass = harness._Hass()
        self.addCleanup(setattr, config_flow, "H500Client",
                        config_flow.H500Client)
        config_flow.H500Client = _SetupClient
        _SetupClient.made = []
        return flow

    def _submit(self, outcome):
        flow = self._flow()
        _SetupClient.outcome = outcome
        result = run(flow.async_step_user({
            "host": "192.168.11.5", "username": "admin",
            "password": "x", "cloud_password": "y", "poll_interval": 5}))
        return result, _SetupClient.made[0]

    def test_a_working_hub_creates_the_entry_and_hangs_up(self):
        result, client = self._submit([{"device_id": "cam0"}])
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(client.closes, 1,
                         "one login, always closed -- this hub wedges under "
                         "repeated authentication")

    def test_the_interval_lands_in_options_where_it_is_read(self):
        """Left in data it would be recorded, ignored, and silently replaced
        by the default."""
        result, _ = self._submit([{"device_id": "cam0"}])
        self.assertEqual(result["options"], {"poll_interval": 5})
        self.assertNotIn("poll_interval", result["data"])

    def test_a_named_refusal_says_check_your_password(self):
        result, client = self._submit(api.H500AuthError("-40414"))
        self.assertEqual(result["errors"], {"base": "invalid_auth"})
        self.assertEqual(client.closes, 1)

    def test_anything_vaguer_does_not_blame_the_password(self):
        """A wedge, a timeout, a garbage answer: "check your password" is
        the wrong thing to tell somebody whose password is fine."""
        result, client = self._submit(OSError("no route"))
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        self.assertEqual(client.closes, 1)

    def test_a_hub_with_no_cameras_is_named_as_such(self):
        result, _ = self._submit([])
        self.assertEqual(result["errors"], {"base": "no_cameras"})


if __name__ == "__main__":
    unittest.main()
