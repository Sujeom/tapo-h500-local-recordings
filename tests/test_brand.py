"""Brand images ship inside the integration and are the sizes the spec wants.

Since Home Assistant 2026.3 a custom integration serves its own brand images
from a `brand/` directory beside `manifest.json`, and the brands repository
auto-closes pull requests for `custom_integrations/*`. So these files are the
only thing standing between this integration and a placeholder icon, and a
wrong size or a wrong filename fails silently -- the icon simply does not
appear, with nothing logged.
"""
import struct
import unittest
from pathlib import Path

BRAND = Path(__file__).parents[1] / "custom_components" / "tapo_h500" / "brand"


def dimensions(name):
    """Width and height straight out of the PNG IHDR chunk."""
    data = (BRAND / name).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{name} is not a PNG")
    return struct.unpack(">II", data[16:24])


class BrandImages(unittest.TestCase):
    def test_they_live_inside_the_integration(self):
        """Beside manifest.json, so installing the component installs them.
        A copy in the repository root is served to nobody."""
        self.assertTrue((BRAND.parent / "manifest.json").is_file())
        for name in ("icon.png", "icon@2x.png", "logo.png", "logo@2x.png"):
            self.assertTrue((BRAND / name).is_file(), f"missing {name}")

    def test_icons_are_square_at_the_two_required_sizes(self):
        self.assertEqual(dimensions("icon.png"), (256, 256))
        self.assertEqual(dimensions("icon@2x.png"), (512, 512))

    def test_logos_sit_inside_the_shortest_side_bands(self):
        """Logos are measured on their shortest side, not as a fixed square."""
        self.assertTrue(128 <= min(dimensions("logo.png")) <= 256)
        self.assertTrue(256 <= min(dimensions("logo@2x.png")) <= 512)

    def test_nothing_is_an_empty_placeholder(self):
        for name in ("icon.png", "icon@2x.png", "logo.png", "logo@2x.png"):
            self.assertGreater((BRAND / name).stat().st_size, 1000, name)


if __name__ == "__main__":
    unittest.main()
