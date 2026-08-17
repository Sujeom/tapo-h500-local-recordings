"""Getting the face names back out again.

Face names and the camera layout are the only state here a hub cannot
reproduce: recordings and settings live on the hub, every sensor is derived,
and these two came out of somebody spending months opening photographs to work
out who a twelve-digit number is. They live on the config entry, so deleting
the entry takes them with it and nothing warns first.

The merge rules are pure and are run for real -- they are where a restore
quietly loses work. The service wiring around them is checked statically,
since it needs the Home Assistant runtime.
"""
import importlib
import re
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
INIT = (COMPONENT / "__init__.py").read_text()
ACTIONS = (COMPONENT / "services.yaml").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

backup = importlib.import_module("tapo_h500.backup")
const = importlib.import_module("tapo_h500.const")

RESTORE_SCHEMA = INIT.split("RESTORE_SCHEMA = vol.Schema(", 1)[1].split(
    "\n})", 1)[0]


class Snapshot(unittest.TestCase):
    def test_it_carries_the_names_and_the_layout(self):
        taken = backup.snapshot({"77": "Alice"}, {"Front": 1})
        self.assertEqual(taken["face_names"], {"77": "Alice"})
        self.assertEqual(taken["camera_order"], {"Front": 1})

    def test_it_carries_a_version(self):
        """By the time there is a second format, every file in the first is
        already out there. Stamping costs nothing now and is impossible
        later."""
        self.assertEqual(backup.snapshot({}, {})["version"],
                         const.BACKUP_VERSION)

    def test_it_copies_rather_than_aliasing(self):
        """The maps it is handed belong to the config entry. Returning them
        directly would let a caller mutate the live options."""
        names = {"77": "Alice"}
        backup.snapshot(names, {})["face_names"]["77"] = "Bob"
        self.assertEqual(names, {"77": "Alice"})

    def test_it_holds_nothing_secret(self):
        """People paste this where they paste diagnostics."""
        self.assertEqual(set(backup.snapshot({}, {})),
                         {"version", "face_names", "camera_order",
                          "settings"})


class MergeNames(unittest.TestCase):
    def test_a_restore_adds_to_what_is_there(self):
        """The usual restore is an older backup onto an entry that has learned
        a few more names since. Replacing there discards them silently."""
        merged = backup.merge_names({"1": "Alice"}, {"2": "Bob"}, False)
        self.assertEqual(merged, {"1": "Alice", "2": "Bob"})

    def test_the_backup_wins_on_a_clash(self):
        merged = backup.merge_names({"1": "Alice"}, {"1": "Alicia"}, False)
        self.assertEqual(merged, {"1": "Alicia"})

    def test_replacing_drops_everything_else(self):
        merged = backup.merge_names({"1": "Alice"}, {"2": "Bob"}, True)
        self.assertEqual(merged, {"2": "Bob"})

    def test_a_blank_name_removes_rather_than_stores(self):
        """The rule the card and the options screen already use. Without it a
        backup with an emptied box restores an entry named ""."""
        merged = backup.merge_names({"1": "Alice"}, {"1": "  "}, False)
        self.assertEqual(merged, {})

    def test_names_are_trimmed(self):
        merged = backup.merge_names({}, {"1": " Alice "}, False)
        self.assertEqual(merged, {"1": "Alice"})

    def test_numeric_ids_become_strings(self):
        """The hub reports ids as numbers and a hand-written backup will have
        them as numbers. A map answering to 77 but not "77" looks empty."""
        merged = backup.merge_names({}, {77: "Alice"}, False)
        self.assertEqual(merged, {"77": "Alice"})

    def test_the_current_map_is_not_mutated(self):
        current = {"1": "Alice"}
        backup.merge_names(current, {"2": "Bob"}, False)
        self.assertEqual(current, {"1": "Alice"})

    def test_an_empty_backup_merged_changes_nothing(self):
        self.assertEqual(backup.merge_names({"1": "Alice"}, {}, False),
                         {"1": "Alice"})

    def test_an_empty_backup_replacing_clears_everything(self):
        self.assertEqual(backup.merge_names({"1": "Alice"}, {}, True), {})


class MergeRanks(unittest.TestCase):
    def test_no_layout_in_the_backup_leaves_the_current_one(self):
        """A backup taken before the layout existed carries no camera_order
        at all, and must not be read as "the layout is empty"."""
        self.assertEqual(backup.merge_ranks({"Front": 1}, None, False),
                         {"Front": 1})

    def test_and_that_holds_when_replacing_too(self):
        self.assertEqual(backup.merge_ranks({"Front": 1}, None, True),
                         {"Front": 1})

    def test_a_layout_merges(self):
        self.assertEqual(backup.merge_ranks({"Front": 1}, {"Side": 0}, False),
                         {"Front": 1, "Side": 0})

    def test_replacing_drops_the_rest(self):
        self.assertEqual(backup.merge_ranks({"Front": 1}, {"Side": 0}, True),
                         {"Side": 0})

    def test_ranks_become_numbers(self):
        self.assertEqual(backup.merge_ranks({}, {"Front": "2"}, False),
                         {"Front": 2})


class RestoredOptions(unittest.TestCase):
    def test_everything_else_in_the_options_survives(self):
        """Home Assistant replaces options wholesale on save. Writing only the
        names deleted the poll interval and everything else, which is a bug
        this integration has already shipped once."""
        options = {"poll_interval": 2, const.CONF_FACE_NAMES: {"1": "Old"}}
        updated = backup.restored_options(options, {"1": "New"}, None)
        self.assertEqual(updated["poll_interval"], 2)
        self.assertEqual(updated[const.CONF_FACE_NAMES], {"1": "New"})

    def test_the_layout_is_untouched_when_there_was_none(self):
        options = {const.CONF_CAMERA_ORDER: {"Front": 1}}
        updated = backup.restored_options(options, {}, None)
        self.assertEqual(updated[const.CONF_CAMERA_ORDER], {"Front": 1})

    def test_and_written_when_there_was(self):
        updated = backup.restored_options({}, {}, {"Front": 3})
        self.assertEqual(updated[const.CONF_CAMERA_ORDER], {"Front": 3})

    def test_the_original_options_are_not_mutated(self):
        options = {"poll_interval": 2}
        backup.restored_options(options, {"1": "Alice"}, None)
        self.assertEqual(options, {"poll_interval": 2})


class Validation(unittest.TestCase):
    def test_the_payload_is_validated_not_trusted(self):
        """This writes into the config entry. A restore is exactly when
        somebody pastes a hand-edited blob, and a bad value would sit there
        until something tripped over it a long way from here."""
        self.assertIn("cv.string: cv.string", RESTORE_SCHEMA)

    def test_ranks_must_be_numbers_in_range(self):
        self.assertIn("vol.Range(min=0, max=20)", RESTORE_SCHEMA)

    def test_names_are_required(self):
        self.assertIn('vol.Required("face_names")', RESTORE_SCHEMA)

    def test_replacing_is_opt_in(self):
        self.assertIn('vol.Optional("replace", default=False)', RESTORE_SCHEMA)


class Registration(unittest.TestCase):
    def test_both_are_registered(self):
        self.assertIn("(SERVICE_BACKUP_NAMES, backup_names, BACKUP_SCHEMA)",
                      INIT)
        self.assertIn("(SERVICE_RESTORE_NAMES, restore_names, RESTORE_SCHEMA)",
                      INIT)

    def test_the_backup_action_writes_nothing(self):
        body = INIT.split("    async def backup_names(", 1)[1].split(
            "\n    async def ", 1)[0]
        self.assertNotIn("async_update_entry", body)

    def test_every_registered_service_is_removed_on_unload(self):
        registered = set(re.findall(r"\(SERVICE_(\w+), \w+, \w+_SCHEMA\)", INIT))
        listed = set(re.findall(
            r"SERVICE_(\w+)",
            INIT.split("SERVICES = (", 1)[1].split("\n)", 1)[0]))
        self.assertEqual(registered - listed, set())

    def test_the_removal_list_names_only_services(self):
        """It had collected signals, option keys and a prompt string. Nothing
        broke -- has_service simply answered no -- which is why nobody
        noticed, and it made the list useless as a statement of intent."""
        listed = [name for name in re.findall(
            r"\n    (\w+),", INIT.split("SERVICES = (", 1)[1].split("\n)", 1)[0])]
        self.assertTrue(listed)
        for name in listed:
            self.assertTrue(name.startswith("SERVICE_"), name)

    def test_both_are_described_for_the_ui(self):
        self.assertIn("\nbackup_names:", ACTIONS)
        self.assertIn("\nrestore_names:", ACTIONS)


if __name__ == "__main__":
    unittest.main()


class Settings(unittest.TestCase):
    """Every option somebody typed rides in the backup, not just the names.

    Sensitivity, the night window, download types, keep counts, silent
    hours and the card days are all decisions a reinstall loses and a hub
    cannot reproduce -- the same argument that put the names here. Only
    options that exist are carried: absent stays absent, so restoring an
    old backup does not overwrite a newer decision with a default.
    """

    def test_the_authored_options_are_listed(self):
        for name in ("sensitivity", "night_start", "night_end",
                     "download_types", "keep_downloads", "keep_rings",
                     "keep_person", "silent_hours", "card_days",
                     "auto_download", "convert_mp4"):
            self.assertIn(name, backup.USER_OPTIONS, name)

    def test_credentials_and_plumbing_never_ride_along(self):
        for name in ("poll_interval", "face_names", "camera_order",
                     "cloud_password", "password", "host"):
            self.assertNotIn(name, backup.USER_OPTIONS, name)

    def test_snapshot_carries_only_the_options_that_exist(self):
        taken = backup.snapshot({}, {}, {"night_start": 23, "junk": 1})
        self.assertEqual(taken["settings"], {"night_start": 23})

    def test_an_old_backup_without_settings_restores_cleanly(self):
        merged = backup.merge_settings({"night_start": 23}, None)
        self.assertEqual(merged, {"night_start": 23})

    def test_restored_settings_replace_only_what_they_name(self):
        merged = backup.merge_settings(
            {"night_start": 23, "card_days": 7},
            {"night_start": 22, "junk": "ignored"})
        self.assertEqual(merged, {"night_start": 22, "card_days": 7})

    def test_restored_options_carry_the_settings_through(self):
        options = backup.restored_options(
            {"poll_interval": 2, "night_start": 23}, {}, None,
            settings={"night_start": 22, "card_days": 7})
        self.assertEqual(options["night_start"], 22)
        self.assertEqual(options["card_days"], 7)
        self.assertEqual(options["poll_interval"], 2)
