"""Two hubs.

Most of it already worked: the coordinators are keyed per config entry, the
services all take one, and the cards already accept an entry id. What did not
work was anything keyed on a camera's name -- two hubs can each have a "Front
Doorbell", and the name is what a summary is keyed on and what a download's
folder is called.

The summary silently dropped one camera. The folder silently mixed two
cameras' recordings, and "already downloaded" was then answered for one by the
other.
"""
import importlib
import json
import re
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
INIT = (COMPONENT / "__init__.py").read_text()
INTENT = (COMPONENT / "intent.py").read_text()
REPAIRS = (COMPONENT / "repairs.py").read_text()
CARD = (COMPONENT / "www" / "tapo-h500-card.js").read_text()
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text())

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")


class Distinct(unittest.TestCase):
    def test_unique_names_are_left_alone(self):
        """The single-hub case, which is nearly everyone. A hub address in
        every spoken sentence would be a cost paid by all to fix a case few
        have."""
        self.assertEqual(clips.distinct([("Front", "a"), ("Back", "b")]),
                         ["Front", "Back"])

    def test_a_clash_qualifies_both(self):
        """Not just the second. "Front Doorbell" beside "Front Doorbell
        (192.168.11.5)" is worse than neither being bare, because the first
        reads as the real one."""
        self.assertEqual(
            clips.distinct([("Front", "hub one"), ("Front", "hub two")]),
            ["Front (hub one)", "Front (hub two)"])

    def test_a_clash_does_not_qualify_the_others(self):
        found = clips.distinct(
            [("Front", "one"), ("Back", "one"), ("Front", "two")])
        self.assertEqual(found, ["Front (one)", "Back", "Front (two)"])

    def test_order_is_kept(self):
        """The caller zips this back against its own list."""
        pairs = [("C", "x"), ("A", "y"), ("B", "z")]
        self.assertEqual(clips.distinct(pairs), ["C", "A", "B"])

    def test_nothing_at_all(self):
        self.assertEqual(clips.distinct([]), [])

    def test_three_of_a_name(self):
        found = clips.distinct([("F", "1"), ("F", "2"), ("F", "3")])
        self.assertEqual(found, ["F (1)", "F (2)", "F (3)"])


class Summaries(unittest.TestCase):
    def test_the_spoken_summary_covers_every_hub(self):
        walk = INTENT.split("class TodayIntent", 1)[1]
        self.assertIn("for coordinator in _hubs(hass)", walk)

    def test_it_does_not_key_cameras_by_name_alone(self):
        """A dictionary keyed on the alias drops one of two cameras sharing
        it, and the answer then describes half the house as though it were
        all of it."""
        walk = INTENT.split("class TodayIntent", 1)[1]
        self.assertIn("distinct(", walk)

    def test_the_summary_action_does_the_same(self):
        body = INIT.split("    async def daily_summary(", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertIn("distinct(", body)

    def test_the_last_event_answer_already_covered_every_hub(self):
        walk = INTENT.split("class LastEventIntent", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn("for coordinator in _hubs(hass)", walk)


class ClashingFolders(unittest.TestCase):
    def _body(self):
        return REPAIRS.split("def _clashing_names(", 1)[1].split(
            "\n\ndef ", 1)[0]

    def test_the_check_runs(self):
        self.assertIn("_clashing_names(hass, entry_id, coordinator)", REPAIRS)

    def test_it_compares_across_every_loaded_hub(self):
        """Within one hub a clash is unlikely; across two it is the normal
        way to name a front door."""
        self.assertIn("DATA_HUBS", self._body())

    def test_it_compares_the_slug_rather_than_the_alias(self):
        """The folder is named after the slug, so aliases that differ only in
        case or spacing are one directory on disk while looking like two
        cameras in the app -- and that is the collision nobody would spot."""
        both = [{"alias": "Front Door"}, {"alias": "front  door"}]
        self.assertEqual(clips.clashing_names(both, both), ["front_door"])
        self.assertNotEqual(clips.camera_slug(both[0]),
                            clips.camera_slug({"alias": "front-door"}))

    def test_genuinely_different_names_do_not_clash(self):
        cameras = [{"alias": "Front Door"}, {"alias": "Back Gate"}]
        self.assertEqual(clips.clashing_names(cameras, cameras), [])

    def test_a_clash_on_another_hub_is_not_reported_here(self):
        theirs = [{"alias": "Shed"}, {"alias": "Shed"}]
        mine = [{"alias": "Front Door"}]
        self.assertEqual(clips.clashing_names(theirs + mine, mine), [])

    def test_a_clash_between_hubs_is(self):
        mine = [{"alias": "Front Door"}]
        theirs = [{"alias": "Front Door"}]
        self.assertEqual(clips.clashing_names(theirs + mine, mine),
                         ["front_door"])

    def test_one_camera_never_clashes_with_itself(self):
        mine = [{"alias": "Front Door"}]
        self.assertEqual(clips.clashing_names(mine, mine), [])

    def test_it_clears_itself(self):
        self.assertIn("async_delete_issue", self._body())

    def test_the_issue_is_described(self):
        self.assertIn("clashing_camera_names", STRINGS["issues"])
        described = STRINGS["issues"]["clashing_camera_names"]["description"]
        self.assertIn("{cameras}", described)
        # It has to say what to do, not only what is wrong.
        self.assertIn("Rename", described)

    def test_the_path_layout_is_not_quietly_changed(self):
        """Putting the hub into the path would orphan every recording anyone
        has already downloaded, to fix a case most installations do not
        have."""
        media = (COMPONENT / "media.py").read_text()
        camera_dir = media.split("def camera_dir(", 1)[1].split(
            "\n\ndef ", 1)[0]
        self.assertNotIn("entry", camera_dir)


class Cards(unittest.TestCase):
    def test_the_hub_is_chosen_from_a_list(self):
        """It only matters with more than one hub, which is exactly when
        nobody knows the opaque id to type in."""
        self.assertIn('selector: { config_entry: { integration: "tapo_h500" } }',
                      CARD)

    def test_leaving_it_empty_still_uses_the_first_hub(self):
        """Existing single-hub cards carry no entry_id and must keep
        working."""
        resolver = CARD.split("async _entryId()", 1)[1].split("\n  async ", 1)[0]
        self.assertIn("if (this._config.entry_id) return this._config.entry_id",
                      resolver)
        self.assertIn("entries[0].entry_id", resolver)

    def test_every_call_passes_the_chosen_hub(self):
        """A card that resolved the hub once and then called a service
        without it would answer for whichever hub loaded first."""
        for call in re.findall(r"config_entry_id: ([^,\n]+)", CARD):
            self.assertIn("_entryId()", call)


class Setup(unittest.TestCase):
    def test_services_are_registered_once_rather_than_per_hub(self):
        self.assertIn(
            "if not hass.services.has_service(DOMAIN, SERVICE_LIST_RECORDINGS)",
            INIT)

    def test_services_are_removed_only_when_the_last_hub_unloads(self):
        """Unloading one of two must not take the actions away from the
        other."""
        unload = INIT.split("async def async_unload_entry", 1)[1]
        self.assertIn("if not hubs:", unload)

    def test_the_card_is_registered_once(self):
        self.assertIn("if data.get(DATA_CARD):", INIT)

    def test_the_preview_view_is_registered_once(self):
        self.assertIn("if not hass.data[DOMAIN].get(DATA_PREVIEW):", INIT)

    def test_each_hub_is_its_own_device(self):
        sensor = (COMPONENT / "sensor.py").read_text()
        hub_device = sensor.split("def hub_device(", 1)[1].split(
            "\n\nclass ", 1)[0]
        self.assertIn("(DOMAIN, entry.entry_id)", hub_device)


if __name__ == "__main__":
    unittest.main()
