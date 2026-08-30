"""How many hub calls Home Assistant may make at once, per platform.

Unset, PARALLEL_UPDATES means no limit: a scene touching four entities of one
platform fires four writes simultaneously. This hub wedges under concurrent
sessions -- the one login, the hub lock and the media lock all exist for that
reason -- so the value is dictated by the hardware rather than by convention.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402,F401  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

# One at a time: every entity here can command the hub.
COMMANDS = ("switch", "number", "select", "siren", "button", "update",
            "camera")
# Unlimited: these read what the coordinator's one poll already fetched.
READS = ("binary_sensor", "sensor", "event", "image", "calendar")


class EveryPlatformSaysHowManyAtOnce(unittest.TestCase):
    def _value(self, name):
        module = importlib.import_module(f"tapo_h500.{name}")
        self.assertTrue(
            hasattr(module, "PARALLEL_UPDATES"),
            f"{name}.py does not say how many calls it allows at once")
        return module.PARALLEL_UPDATES

    def test_every_platform_declares_it(self):
        for name in COMMANDS + READS:
            with self.subTest(platform=name):
                self._value(name)

    def test_the_command_platforms_allow_exactly_one(self):
        """Not zero. A scene that turns off the LED, silences the siren and
        flips two switches would otherwise open four sessions at once against
        a hub that recovers from that only on a timeout."""
        for name in COMMANDS:
            with self.subTest(platform=name):
                self.assertEqual(self._value(name), 1)

    def test_the_read_platforms_are_unlimited(self):
        """Nothing here polls the hub itself, so there is nothing to
        serialise -- and a limit would queue state writes for no reason."""
        for name in READS:
            with self.subTest(platform=name):
                self.assertEqual(self._value(name), 0)

    def test_the_camera_counts_as_a_command(self):
        """Its image path can reach the coordinator's frame fetch, which opens
        a media session. Two dashboards showing one camera must not ask
        twice at once."""
        self.assertIn("camera", COMMANDS)


if __name__ == "__main__":
    unittest.main()
