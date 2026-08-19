"""The example automations parse and reference only what exists."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.yaml"))


class Examples(unittest.TestCase):
    def test_there_are_examples_and_they_all_parse(self):
        self.assertGreaterEqual(len(EXAMPLES), 3)
        for path in EXAMPLES:
            yaml.safe_load(path.read_text())

    def test_the_cast_example_waits_before_asking(self):
        """Casting without the wait would cast the PREVIOUS clip -- the
        exact class of bug the notification blueprint had."""
        text = (ROOT / "examples" / "cast-last-clip.yaml").read_text()
        self.assertIn("wait_template", text)
        self.assertIn("downloaded", text)
        self.assertIn("media_content_id", text)

    def test_the_backup_example_uses_the_real_service_shape(self):
        text = (ROOT / "examples" / "weekly-name-backup.yaml").read_text()
        self.assertIn("tapo_h500.backup_names", text)
        self.assertIn("response_variable", text)

    def test_examples_never_carry_real_identifiers(self):
        for path in EXAMPLES:
            text = path.read_text()
            self.assertNotIn("192.168.11", text, path.name)
            self.assertIn("YOUR_CONFIG_ENTRY_ID", text) \
                if "config_entry_id" in text else None


if __name__ == "__main__":
    unittest.main()
