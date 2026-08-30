"""The artwork Home Assistant shows for this integration.

The frontend fetches integration artwork from the brands CDN by domain, not
from the integration's own folder -- so until the domain is in
home-assistant/brands under custom_integrations/, the Add integration dialog
and every device page show a generic placeholder. That is the first thing
anybody sees.

The brands repository checks dimensions strictly and rejects on them, so they
are checked here first: iterating in somebody else's review queue is a slow
way to find out an icon is twice the size it should be.
"""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
# The copies that ship inside the integration, which are the ones that meet
# the brands specification. The repository root also carries a set at twice
# these sizes, kept for the README banner and not for submission.
BRAND = ROOT / "custom_components" / "tapo_h500" / "brand"

# What home-assistant/brands requires of an icon.
ICON_SIZES = {"icon.png": (256, 256), "icon@2x.png": (512, 512)}


def png_header(path: Path):
    """(width, height, colour type) without an image library."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height, data[25]


RGBA = 6


class TheIconsAreSubmittable(unittest.TestCase):
    def test_both_icons_exist(self):
        for name in ICON_SIZES:
            with self.subTest(icon=name):
                self.assertTrue((BRAND / name).is_file(), name)

    def test_they_are_exactly_the_sizes_brands_asks_for(self):
        """Not approximately. The repository's own check is exact, and an
        icon at twice the size is rejected without being looked at."""
        for name, expected in ICON_SIZES.items():
            with self.subTest(icon=name):
                width, height, _ = png_header(BRAND / name)
                self.assertEqual((width, height), expected)

    def test_they_are_square(self):
        for name in ICON_SIZES:
            with self.subTest(icon=name):
                width, height, _ = png_header(BRAND / name)
                self.assertEqual(width, height)

    def test_they_carry_an_alpha_channel(self):
        """An icon on a white square looks wrong on every dark theme, and
        the brands check refuses one."""
        for name in ICON_SIZES:
            with self.subTest(icon=name):
                self.assertEqual(png_header(BRAND / name)[2], RGBA)

    def test_the_2x_is_twice_the_1x(self):
        one = png_header(BRAND / "icon.png")[:2]
        two = png_header(BRAND / "icon@2x.png")[:2]
        self.assertEqual(two, (one[0] * 2, one[1] * 2))


class TheREADMEBannerIsSeparate(unittest.TestCase):
    """The root brand/ set is for the README, at twice the icon sizes and
    with logos wider than brands accepts. Asserted so nobody submits those by
    mistake, and so the two sets cannot silently converge."""

    ROOT_BRAND = ROOT / "brand"

    def test_it_exists_and_is_not_the_submittable_set(self):
        submittable = png_header(BRAND / "icon.png")[:2]
        banner = png_header(self.ROOT_BRAND / "icon.png")[:2]
        self.assertNotEqual(banner, submittable)

    def test_the_readme_uses_it(self):
        self.assertIn("brand/logo.png", (ROOT / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
