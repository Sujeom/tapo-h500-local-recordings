"""How busy a camera has to get before it counts as unusual.

The baseline was already the camera's own rate, which handles a busy door and
a quiet gate seeing different amounts. What it could not handle was the two
meaning different things: three times typical is a Saturday on a doorbell
facing a pavement, and somebody in the garden on a back gate.

The important properties are that an installation configured before this
existed behaves exactly as it did, and that a stored level this version has
never heard of does not take a camera's alarm away.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
BINARY_SENSOR = (COMPONENT / "binary_sensor.py").read_text()
CONFIG_FLOW = (COMPONENT / "config_flow.py").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")

CAMERAS = [{"device_id": "cam0", "alias": "Front Doorbell"},
           {"device_id": "cam1", "alias": "Back Gate"}]


class _Client:
    def cameras(self):
        return list(CAMERAS)

    def recent(self, camera, start, end):
        return []

    def detections(self, camera, start, end):
        return []

    def hub_status(self):
        return {}


def build(**options):
    coord = coordinator_mod.H500Coordinator(
        harness._Hass(), harness._Entry(20, **options), _Client())
    coord.cameras = list(CAMERAS)
    return coord


class Levels(unittest.TestCase):
    def test_normal_is_what_has_always_been_used(self):
        """An installation configured before this existed must behave exactly
        as it did."""
        self.assertEqual(const.SENSITIVITY_LEVELS["normal"],
                         (const.UNUSUAL_MULTIPLIER, const.UNUSUAL_FLOOR))

    def test_sensitive_flags_sooner_than_normal(self):
        sensitive = const.SENSITIVITY_LEVELS["sensitive"]
        normal = const.SENSITIVITY_LEVELS["normal"]
        self.assertLess(sensitive[0], normal[0])
        self.assertLess(sensitive[1], normal[1])

    def test_relaxed_flags_later(self):
        relaxed = const.SENSITIVITY_LEVELS["relaxed"]
        normal = const.SENSITIVITY_LEVELS["normal"]
        self.assertGreater(relaxed[0], normal[0])
        self.assertGreater(relaxed[1], normal[1])


class Lookup(unittest.TestCase):
    def test_an_unconfigured_camera_gets_the_old_behaviour(self):
        self.assertEqual(build().sensitivity(0),
                         const.SENSITIVITY_LEVELS["normal"])

    def test_a_configured_camera_gets_its_level(self):
        coord = build(sensitivity={"Back Gate": "sensitive"})
        self.assertEqual(coord.sensitivity(1),
                         const.SENSITIVITY_LEVELS["sensitive"])

    def test_the_other_camera_is_unaffected(self):
        """Per camera is the whole point; one setting reaching both would be
        the global constant again with more steps."""
        coord = build(sensitivity={"Back Gate": "sensitive"})
        self.assertEqual(coord.sensitivity(0),
                         const.SENSITIVITY_LEVELS["normal"])

    def test_it_is_keyed_by_name_rather_than_position(self):
        """An index shifts when a camera is unpaired and a name does not."""
        coord = build(sensitivity={"Back Gate": "relaxed"})
        coord.cameras = list(reversed(CAMERAS))
        self.assertEqual(coord.sensitivity(0),
                         const.SENSITIVITY_LEVELS["relaxed"])

    def test_a_level_this_version_never_heard_of_falls_back(self):
        """Rather than raising, which would take the camera's alarm away for
        a reason nobody could see."""
        coord = build(sensitivity={"Front Doorbell": "paranoid"})
        self.assertEqual(coord.sensitivity(0),
                         const.SENSITIVITY_LEVELS["normal"])

    def test_an_index_past_the_paired_list_falls_back(self):
        self.assertEqual(build().sensitivity(99),
                         const.SENSITIVITY_LEVELS["normal"])


class Entity(unittest.TestCase):
    def _body(self):
        return BINARY_SENSOR.split("class H500UnusualActivity", 1)[1].split(
            "\n\nclass ", 1)[0]

    def test_it_asks_the_coordinator_rather_than_the_constants(self):
        body = self._body()
        self.assertIn("self.coordinator.sensitivity(self.index)", body)
        self.assertNotIn("UNUSUAL_MULTIPLIER", body)
        self.assertNotIn("UNUSUAL_FLOOR", body)

    def test_it_says_what_it_is_measuring_against(self):
        """So "why has this not fired" is answerable from the entity rather
        than from the source."""
        body = self._body()
        self.assertIn('"multiplier"', body)
        self.assertIn('"minimum_per_hour"', body)


class Form(unittest.TestCase):
    def test_there_is_a_screen_for_it(self):
        self.assertIn("async_step_sensitivity", CONFIG_FLOW)

    def test_it_is_on_the_menu(self):
        menu = CONFIG_FLOW.split("async_step_init", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('"sensitivity"', menu)
        self.assertIn("sensitivity",
                      STRINGS["options"]["step"]["init"]["menu_options"])

    def test_it_offers_the_levels_rather_than_the_numbers(self):
        """A multiplier and a floor are the right model for the code and the
        wrong question to ask a person."""
        step = CONFIG_FLOW.split("async_step_sensitivity", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("options=list(SENSITIVITY_LEVELS)", step)

    def test_every_level_has_a_label(self):
        offered = set(const.SENSITIVITY_LEVELS)
        labelled = set(STRINGS["selector"]["sensitivity"]["options"])
        self.assertEqual(offered - labelled, set())

    def test_saving_it_keeps_the_other_options(self):
        """Options are replaced wholesale on save. Writing only this screen
        deleted every face name, which has happened here before."""
        step = CONFIG_FLOW.split("async_step_sensitivity", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("**self.config_entry.options", step)

    def test_it_keeps_levels_for_cameras_not_on_the_form(self):
        """A camera that is temporarily unpaired must not lose its setting."""
        step = CONFIG_FLOW.split("async_step_sensitivity", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("levels.update(", step)

    def test_it_has_a_screen_title(self):
        self.assertIn("sensitivity", STRINGS["options"]["step"])


if __name__ == "__main__":
    unittest.main()
