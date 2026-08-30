"""The hub restart button: one press, one verb, never automatic.

pytapo's standard immediate-reboot call, rebootDevice -- a different and
unambiguous method from setReboot, which this integration still refuses to
touch because its parameters cannot be told apart from editing the nightly
schedule. A reboot verb has two failure shapes that mean opposite things:
a protocol refusal (the hub said no) must surface, while the connection
dropping mid-acknowledgement is what SUCCESS looks like when the device
you asked is the device carrying the answer.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
BUTTON = (COMPONENT / "button.py").read_text()
INIT = (COMPONENT / "__init__.py").read_text()
# The thirteen service handlers moved out of the package body.
SERVICES_SRC = (COMPONENT / "services.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402,F401  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

api = importlib.import_module("tapo_h500.api")


def _import_button():
    """The real button module, over the thinnest possible stubs."""
    import types
    if "tapo_h500.button" in sys.modules:
        return sys.modules["tapo_h500.button"]
    button = types.ModuleType("homeassistant.components.button")
    button.ButtonEntity = type("ButtonEntity", (), {})
    button.ButtonDeviceClass = types.SimpleNamespace(RESTART="restart")
    sys.modules.setdefault("homeassistant.components.button", button)
    const = types.ModuleType("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(CONFIG="config",
                                                 DIAGNOSTIC="diagnostic")
    sys.modules.setdefault("homeassistant.const", const)
    platform = types.ModuleType("homeassistant.helpers.entity_platform")
    platform.AddEntitiesCallback = object
    sys.modules.setdefault(
        "homeassistant.helpers.entity_platform", platform)
    update = sys.modules["homeassistant.helpers.update_coordinator"]
    if not hasattr(update, "CoordinatorEntity"):
        class CoordinatorEntity:
            def __init__(self, coordinator):
                self.coordinator = coordinator

            def __class_getitem__(cls, item):
                return cls
        update.CoordinatorEntity = CoordinatorEntity
    # hub_device comes from sensor.py, which drags in far more of Home
    # Assistant than a button test needs.
    sensor = types.ModuleType("tapo_h500.sensor")
    sensor.hub_device = lambda coordinator, entry: {}
    sys.modules.setdefault("tapo_h500.sensor", sensor)
    return importlib.import_module("tapo_h500.button")


class _FakeHub:
    def __init__(self, boom=None):
        self.calls = []
        self.boom = boom

    def executeFunction(self, method, params):
        self.calls.append((method, params))
        if self.boom is not None:
            raise self.boom
        return {}


class TheVerb(unittest.TestCase):
    def test_it_sends_pytapos_reboot_shape(self):
        client = api.H500Client("host", "admin", "local", "cloud")
        client._hub = _FakeHub()
        client.reboot()
        self.assertEqual(client._hub.calls, [
            ("rebootDevice", {"system": {"reboot": "null"}})])

    def test_the_verb_lives_in_exactly_one_place(self):
        """Nothing in the integration may grow the ability to reboot the
        hub as a side effect -- one string, in the client, reached only by
        the button somebody pressed."""
        holders = [path.name for path in COMPONENT.glob("*.py")
                   if "rebootDevice" in path.read_text()]
        self.assertEqual(holders, ["api.py"])

    def test_setreboot_is_still_never_called(self):
        """The ambiguous verb stays excluded; this button changes nothing
        about that."""
        for path in COMPONENT.glob("*.py"):
            self.assertNotIn('executeFunction("setReboot"', path.read_text(),
                             path.name)


class TheButton(unittest.TestCase):
    def test_the_platform_is_registered(self):
        self.assertIn("Platform.BUTTON", INIT)

    def test_it_is_a_restart_button_on_the_hub_device(self):
        self.assertIn("ButtonDeviceClass.RESTART", BUTTON)
        self.assertIn("hub_device", BUTTON)

    def test_the_press_runs_in_an_executor(self):
        body = BUTTON.split("async def async_press", 1)[1]
        self.assertIn("async_add_executor_job", body)
        self.assertIn("client.reboot", body)

    def test_a_refusal_surfaces_and_a_dropped_connection_does_not(self):
        """-40xxx means the hub said no and the person must hear it; the
        socket dying mid-acknowledgement means the reboot is happening.

        Pressed for real rather than grepped for: a guard that is only
        source-matched can be turned into `if False` without any test
        noticing -- measured, once.
        """
        import asyncio
        import types

        button_mod = _import_button()
        errors = importlib.import_module("homeassistant.exceptions")

        def press(outcome):
            entity = object.__new__(button_mod.H500RestartButton)

            def reboot():
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            entity.coordinator = types.SimpleNamespace(
                client=types.SimpleNamespace(reboot=reboot))

            class _Hass:
                async def async_add_executor_job(self, fn, *args):
                    return fn(*args)

            entity.hass = _Hass()
            asyncio.run(entity.async_press())

        with self.assertRaises(errors.HomeAssistantError):
            press(RuntimeError("Error: -40209 in response"))
        press(ConnectionResetError("peer went away"))   # success-shaped
        press({})                                       # acknowledged

    def test_the_button_has_words_on_it(self):
        strings = json.loads(
            (COMPONENT / "translations" / "en.json").read_text())
        self.assertTrue(strings["entity"]["button"]["restart"]["name"])


if __name__ == "__main__":
    unittest.main()
