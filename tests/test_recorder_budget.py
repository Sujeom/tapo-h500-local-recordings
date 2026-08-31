"""What every state change writes to the database.

Home Assistant stores a full copy of an entity's attributes with every state
change, so an attribute holding a list is written to disk for as long as the
purge keeps it. That is the most common way a Home Assistant database reaches
gigabytes, and it is invisible until it is severe.

This integration does not have that problem, and the measurement is why it is
worth keeping rather than assuming. Every attribute here is a scalar or a
fixed-width summary: the whole set is under two kilobytes, and forty times the
recordings moves it by one percent. Nothing accumulates.

So there is no recorder platform excluding anything -- adding one would be
speculative work against a problem that was measured and is not there. What
there is instead is this budget, which fails the day somebody attaches a list
of recordings to a sensor.
"""
import asyncio
import importlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

dt_util = sys.modules["homeassistant.util.dt"]
NOW = int(dt_util.utcnow().timestamp())

# Measured, not chosen. Total across every entity that reports attributes at
# all, and the largest single one. Both have room for an honest addition and
# neither has room for a collection.
TOTAL_BUDGET = 3_000
LARGEST_BUDGET = 400

PLATFORMS = ("binary_sensor", "calendar", "camera", "event", "image", "number",
             "select", "sensor", "siren", "switch", "update", "button")


def _clip(start, face=None):
    made = {"startTime": start, "endTime": start + 15,
            "events_1": (1 << 1) | (1 << 5) | (1 << 19)}
    if face is not None:
        made["event_info"] = [{"face_id": face}]
    return made


def _attributes(clips=300, faces=3, cameras=2):
    coord, client = harness._build()
    coord.cameras = [{"device_id": f"cam{n}", "alias": f"C{n}",
                      "device_model": "TD21"} for n in range(cameras)]
    client.siren_tones = lambda: ["Doorbell"]
    coord.client = client
    day = [_clip(NOW - index * 30, face=272465657857 + (index % faces))
           for index in range(clips)]
    coord.clips_for = lambda index: list(day)
    coord.entry.options = {
        **coord.entry.options,
        "face_names": {str(272465657857 + n): f"P{n}" for n in range(faces)}}
    coord.entry.async_on_unload = lambda unsub: None
    hass = harness._hass_with(coord)
    made = []
    for name in PLATFORMS:
        module = importlib.import_module(f"tapo_h500.{name}")
        asyncio.run(module.async_setup_entry(hass, coord.entry, made.extend))
    sizes = {}
    for entity in made:
        try:
            attributes = entity.extra_state_attributes
        except Exception:  # noqa: BLE001 - an entity that cannot report is fine
            continue
        if not attributes:
            continue
        sizes[entity.unique_id] = len(json.dumps(attributes, default=str))
    return sizes


class EveryStateWriteIsSmall(unittest.TestCase):
    def setUp(self):
        self.sizes = _attributes()

    def test_the_whole_set_fits_in_the_budget(self):
        self.assertLess(sum(self.sizes.values()), TOTAL_BUDGET)

    def test_no_single_entity_carries_much(self):
        worst = max(self.sizes.items(), key=lambda row: row[1])
        self.assertLess(worst[1], LARGEST_BUDGET, f"{worst[0]} is the largest")

    def test_some_entities_do_report_attributes(self):
        """Otherwise the budget above passes on an empty measurement."""
        self.assertGreater(len(self.sizes), 20)


class NothingAccumulates(unittest.TestCase):
    """The failure this is really guarding against.

    A total that is small today but grows with the number of recordings is a
    database problem waiting for a busy week.
    """

    def test_forty_times_the_recordings_barely_moves_it(self):
        small = sum(_attributes(clips=100).values())
        large = sum(_attributes(clips=4000).values())
        self.assertLess(large, small * 1.2,
                        f"{small} bytes became {large} with forty times the "
                        f"recordings; something is keeping a list")

    def test_nor_does_a_household_the_hub_keeps_seeing(self):
        few = sum(_attributes(faces=2).values())
        many = sum(_attributes(faces=12).values())
        self.assertLess(many, few * 2.5,
                        "an attribute grows with the number of faces")


if __name__ == "__main__":
    unittest.main()
