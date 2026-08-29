"""The day in one picture.

A doorbell produces dozens of near-identical fifteen-second clips, and looking
through them means opening dozens of things.

ffmpeg is on this machine and is already how every thumbnail here gets made, so
the sheet is built for real and measured, rather than asserted about. The
layout arithmetic is pure and is run directly.
"""
import asyncio
import importlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
IMAGE = (COMPONENT / "image.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

FFMPEG = shutil.which("ffmpeg")


def _stub_ffmpeg():
    """Install the only ffmpeg stub that can actually run ffmpeg.

    Assignment, not `setdefault`. The general harness manufactures a name for
    anything it is asked for, so whether this file wins came down to whether
    something else imported ffmpeg first -- which is decided by test file
    names, and quietly changed every time one is added. It broke twice. This
    stub is a strict superset of the manufactured one, so taking the name
    outright costs nothing and stops the ordering mattering.
    """
    module = types.ModuleType("homeassistant.components.ffmpeg")
    module.get_ffmpeg_manager = lambda hass: types.SimpleNamespace(
        binary=FFMPEG or "ffmpeg")
    sys.modules["homeassistant.components.ffmpeg"] = module
    # Both users bind the name at import, so a module already loaded is
    # holding whatever was there first and will not see this.
    for name in ("tapo_h500.contact_sheet", "tapo_h500._real_media"):
        loaded = sys.modules.get(name)
        if loaded is not None:
            loaded.get_ffmpeg_manager = module.get_ffmpeg_manager
    # contact_sheet asks media where a camera's folder is; the test lays the
    # folder out itself and points this at it.
    media = sys.modules["tapo_h500.media"]
    media.camera_dir = lambda hass, camera: Path(camera["dir"])


_stub_ffmpeg()
sheet = importlib.import_module("tapo_h500.contact_sheet")


def make_jpeg(path: Path, shade: str = "gray"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={shade}:s=640x360:d=1", "-frames:v", "1",
         str(path), "-y"], check=True)


def dimensions(data: bytes) -> tuple[int, int]:
    with tempfile.NamedTemporaryFile(suffix=".jpg") as handle:
        handle.write(data)
        handle.flush()
        out = subprocess.run(
            ["ffprobe", "-hide_banner", "-loglevel", "error",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             handle.name], capture_output=True, text=True, check=True)
    width, height = out.stdout.strip().rstrip(",").split(",")[:2]
    return int(width), int(height)


class Grid(unittest.TestCase):
    def test_a_full_row(self):
        self.assertEqual(sheet.grid(4, 4, 24), (4, 1, 4))

    def test_it_wraps(self):
        self.assertEqual(sheet.grid(5, 4, 24), (4, 2, 5))

    def test_it_is_never_wider_than_there_are_pictures(self):
        """Three recordings in a four-wide grid leave a blank column, which
        reads as a photograph that failed to load."""
        self.assertEqual(sheet.grid(3, 4, 24), (3, 1, 3))

    def test_it_caps(self):
        """A busy street produces hundreds of recordings a day, and forty
        rows is the same scrolling problem in a different shape."""
        self.assertEqual(sheet.grid(500, 4, 24), (4, 6, 24))

    def test_nothing_is_nothing(self):
        """Distinguishable from an empty sheet, which would look like a
        fault."""
        self.assertEqual(sheet.grid(0, 4, 24), (0, 0, 0))

    def test_a_negative_count_is_nothing(self):
        self.assertEqual(sheet.grid(-3, 4, 24), (0, 0, 0))


class Thumbnails(unittest.TestCase):
    def test_they_come_back_in_time_order(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for name in ("174100.jpg", "080000.jpg", "121500.jpg"):
                (folder / name).write_bytes(b"")
            self.assertEqual([path.name for path in sheet._thumbnails(folder)],
                             ["080000.jpg", "121500.jpg", "174100.jpg"])

    def test_videos_are_not_included(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "080000.mp4").write_bytes(b"")
            self.assertEqual(sheet._thumbnails(folder), [])

    def test_a_missing_day_is_empty(self):
        self.assertEqual(sheet._thumbnails(Path("/nowhere/at/all")), [])


class Staging(unittest.TestCase):
    def test_frames_are_numbered_contiguously_from_zero(self):
        """ffmpeg's image2 demuxer reads a numbered sequence and stops at the
        first gap. The real filenames are times of day and full of gaps."""
        with tempfile.TemporaryDirectory() as source, \
                tempfile.TemporaryDirectory() as target:
            pictures = []
            for name in ("080000.jpg", "174100.jpg"):
                path = Path(source) / name
                path.write_bytes(b"x")
                pictures.append(path)
            sheet._stage(pictures, Path(target))
            self.assertEqual(sorted(item.name for item in Path(target).iterdir()),
                             ["000.jpg", "001.jpg"])

    def test_it_links_rather_than_copies(self):
        """This runs whenever a dashboard asks for the picture. Copying two
        dozen files each time to feed a read-only process is work for
        nothing."""
        with tempfile.TemporaryDirectory() as source, \
                tempfile.TemporaryDirectory() as target:
            path = Path(source) / "080000.jpg"
            path.write_bytes(b"x")
            sheet._stage([path], Path(target))
            self.assertTrue((Path(target) / "000.jpg").is_symlink())


@unittest.skipIf(FFMPEG is None, "ffmpeg is not installed")
class Built(unittest.TestCase):
    """The real thing, run and measured."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.camera = {"dir": self.temp.name, "alias": "Front"}
        self.day = "2026-08-13"

    def tearDown(self):
        self.temp.cleanup()

    def _clips(self, count):
        for n in range(count):
            make_jpeg(Path(self.temp.name) / self.day / f"{n:02d}0000.jpg")

    def _build(self, day=None):
        return asyncio.run(sheet.async_contact_sheet(
            harness._Hass(), self.camera, day or self.day))

    def _staged(self, day=None):
        """What was handed to ffmpeg, and whether it was called at all."""
        seen = []
        real = sheet._stage

        def recording(pictures, into):
            seen.append(list(pictures))
            return real(pictures, into)

        sheet._stage = recording
        try:
            self._build(day)
        finally:
            sheet._stage = real
        return seen

    def test_a_quiet_day_has_no_sheet(self):
        self.assertIsNone(self._build())

    def test_six_recordings_tile_four_across(self):
        self._clips(6)
        built = self._build()
        self.assertIsNotNone(built)
        across, down = 4, 2
        width = 8 + across * sheet.TILE_WIDTH + (across - 1) * 2
        height = 8 + down * sheet.TILE_HEIGHT + (down - 1) * 2
        self.assertEqual(dimensions(built), (width, height))

    def test_two_recordings_make_a_two_wide_sheet(self):
        self._clips(2)
        width = 8 + 2 * sheet.TILE_WIDTH + 2
        self.assertEqual(dimensions(self._build())[0], width)

    def test_it_produces_a_jpeg(self):
        self._clips(2)
        self.assertEqual(self._build()[:2], b"\xff\xd8")

    def test_a_day_with_nothing_downloaded_has_no_sheet(self):
        (Path(self.temp.name) / self.day).mkdir(parents=True)
        self.assertIsNone(self._build())

    def test_another_day_is_not_shown(self):
        self._clips(3)
        built = asyncio.run(sheet.async_contact_sheet(
            harness._Hass(), self.camera, "2026-08-12"))
        self.assertIsNone(built)

    def test_nothing_is_left_behind(self):
        """It stages symlinks in a temporary directory on every request, and
        a dashboard asks for this picture repeatedly."""
        self._clips(2)
        before = len(list(Path(tempfile.gettempdir()).glob("tmp*")))
        self._build()
        after = len(list(Path(tempfile.gettempdir()).glob("tmp*")))
        self.assertEqual(after, before)

    def test_it_keeps_the_newest_when_there_are_too_many(self):
        """Capping from the front would fix the sheet on the morning and
        never show the evening."""
        self._clips(30)
        staged = self._staged()[0]
        self.assertEqual(len(staged), sheet.MAX_TILES)
        self.assertEqual(staged[-1].name, "290000.jpg")
        self.assertEqual(staged[0].name, "060000.jpg")

    def test_a_quiet_day_never_reaches_ffmpeg(self):
        """Staging and running a video process to be told there were no
        pictures is work for an answer already known."""
        self.assertEqual(self._staged(), [])

    def test_a_frame_ffmpeg_cannot_read_gives_no_sheet(self):
        """Not an empty one. A zero-byte image is not None, and every caller
        checks for None."""
        folder = Path(self.temp.name) / self.day
        folder.mkdir(parents=True)
        (folder / "080000.jpg").write_bytes(b"this is not a jpeg")
        self.assertIsNone(self._build())


class Entity(unittest.TestCase):
    def test_every_camera_gets_one(self):
        setup = IMAGE.split("async_setup_entry", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("H500ContactSheet(hass, coordinator, index, camera)",
                      setup)

    def test_it_redraws_when_a_clip_downloads_not_when_an_event_fires(self):
        """A sheet is built from thumbnails, and a thumbnail is written by the
        download. Stamping on the event makes the frontend re-fetch an
        unchanged picture several seconds before the new frame exists."""
        body = IMAGE.split("class H500ContactSheet", 1)[1]
        added = body.split("async def async_added_to_hass", 1)[1].split(
            "\n    @callback", 1)[0]
        self.assertIn('signal("image", self.index)', added)
        self.assertNotIn('signal("event"', added)

    def test_it_is_built_on_request_rather_than_held(self):
        body = IMAGE.split("class H500ContactSheet", 1)[1]
        self.assertIn("await async_contact_sheet(", body)


if __name__ == "__main__":
    unittest.main()
