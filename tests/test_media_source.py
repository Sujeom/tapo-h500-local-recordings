"""Folders in the media browser show what is inside them.

Clips already carried their own frame; the camera and date folders above them
were blank tiles, which is a poor way to find yesterday afternoon among thirty
identical grey rectangles.

_poster is pure filesystem work with no Home Assistant in it, so it runs
against a real directory tree here rather than being read.
"""
import contextlib
import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SOURCE = (COMPONENT / "media_source.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)


def _stub_browser():
    """The media browser classes media_source.py builds on.

    Stubbing them and importing the module for real, rather than reading the
    two helpers back out of the source with exec: that worked and broke the
    moment a function moved, which is a test that guards its own line numbers
    instead of the behaviour.
    """
    player = types.ModuleType("homeassistant.components.media_player")
    player.MediaClass = type("MediaClass", (), {"DIRECTORY": "directory",
                                                "VIDEO": "video"})
    player.MediaType = type("MediaType", (), {"VIDEO": "video"})
    sys.modules.setdefault("homeassistant.components.media_player", player)

    browser = types.ModuleType("homeassistant.components.media_source")

    class BrowseMediaSource:
        def __init__(self, **fields):
            self.__dict__.update(fields)

    browser.BrowseMediaSource = BrowseMediaSource
    browser.MediaSource = type("MediaSource", (), {
        "__init__": lambda self, domain: None})
    browser.MediaSourceItem = type("MediaSourceItem", (), {})
    browser.PlayMedia = type("PlayMedia", (), {})
    browser.Unresolvable = type("Unresolvable", (Exception,), {})
    sys.modules.setdefault("homeassistant.components.media_source", browser)

    media = sys.modules["tapo_h500.media"]
    media.media_root = lambda hass: Path("/media")
    media.signed_url = lambda hass, path: f"/signed{path}"


_stub_browser()
media_source = importlib.import_module("tapo_h500.media_source")
poster = media_source._poster
entries = media_source._entries


@contextlib.contextmanager
def scans():
    """Count directory scans, which is what "cheap" means here.

    os.scandir rather than Path.iterdir: both a targeted walk and an rglob
    bottom out there, and patching iterdir catches only the first -- so the
    cheap version and the whole-tree one measured the same.
    """
    counted: list[str] = []
    real = os.scandir

    def counting(path="."):
        counted.append(str(path))
        return real(path)

    os.scandir = counting
    try:
        yield counted
    finally:
        os.scandir = real


class Tree:
    """A media directory laid out the way downloads actually write it."""

    def __init__(self, layout):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in layout:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")

    def __enter__(self):
        return self.root

    def __exit__(self, *_):
        self.temp.cleanup()


class Poster(unittest.TestCase):
    def test_a_day_folder_shows_its_newest_clip(self):
        with Tree(["Front/2026-08-13/080000.jpg",
                   "Front/2026-08-13/174100.jpg"]) as root:
            self.assertEqual(poster(root / "Front" / "2026-08-13").name,
                             "174100.jpg")

    def test_a_camera_folder_shows_its_newest_day(self):
        with Tree(["Front/2026-08-12/090000.jpg",
                   "Front/2026-08-13/080000.jpg"]) as root:
            found = poster(root / "Front")
            self.assertEqual(found.parent.name, "2026-08-13")

    def test_it_walks_back_past_a_day_with_no_thumbnails(self):
        """Clips downloaded before thumbnails existed, or where the frame
        extraction failed. An empty newest day should not blank the tile."""
        with Tree(["Front/2026-08-12/090000.jpg",
                   "Front/2026-08-13/080000.mp4"]) as root:
            found = poster(root / "Front")
            self.assertEqual(found.parent.name, "2026-08-12")

    def test_nothing_at_all_is_none(self):
        with Tree(["Front/2026-08-13/080000.mp4"]) as root:
            self.assertIsNone(poster(root / "Front"))

    def test_a_missing_folder_is_none(self):
        with Tree([]) as root:
            self.assertIsNone(poster(root / "Nothing"))

    def test_videos_are_not_offered_as_thumbnails(self):
        with Tree(["Front/2026-08-13/080000.mp4"]) as root:
            self.assertIsNone(poster(root / "Front" / "2026-08-13"))

    def test_it_does_not_walk_the_whole_tree(self):
        """A camera with a month of recordings holds thousands of files and
        this runs on every browse. Lexical order is chronological order, so
        the newest is the last of the last -- two directory scans, not
        twenty-nine.

        Counted at os.scandir because that is where both a targeted walk and
        an rglob end up; patching Path.iterdir catches only the first, so the
        cheap version and the expensive one looked identical."""
        layout = [f"Front/2026-08-{day:02d}/{hour:02d}0000.jpg"
                  for day in range(1, 29) for hour in range(0, 24, 2)]
        with Tree(layout) as root:
            with scans() as counted:
                poster(root / "Front")
        # The camera folder and the newest day folder inside it.
        self.assertLessEqual(len(counted), 3)


class Entries(unittest.TestCase):
    def test_folders_come_back_with_a_poster(self):
        with Tree(["Front/2026-08-13/080000.jpg"]) as root:
            found = entries(root, None)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][0], "Front")
            self.assertIsNotNone(found[0][1])

    def test_clips_come_back_without_one(self):
        """A clip's frame sits beside it and needs no searching."""
        with Tree(["Front/2026-08-13/080000.mp4"]) as root:
            found = entries(root / "Front" / "2026-08-13", (".mp4",))
            self.assertEqual(found, [("080000.mp4", None)])

    def test_and_are_not_searched_for_one(self):
        """Searching inside a file finds nothing and costs a stat per clip,
        on every browse of a folder that may hold hundreds. Counted at the
        call rather than at the filesystem: the search bails on is_dir(),
        which is a stat and not a directory scan, so a scan count sees
        nothing either way."""
        asked = []
        real = media_source._poster
        media_source._poster = lambda path: asked.append(path) or real(path)
        layout = [f"Front/2026-08-13/{hour:02d}0000.mp4" for hour in range(20)]
        try:
            with Tree(layout) as root:
                entries(root / "Front" / "2026-08-13", (".mp4",))
        finally:
            media_source._poster = real
        self.assertEqual(asked, [])

    def test_names_are_sorted(self):
        with Tree(["Side/2026-08-13/080000.jpg",
                   "Front/2026-08-13/080000.jpg"]) as root:
            self.assertEqual([name for name, _ in entries(root, None)],
                             ["Front", "Side"])


class Child(unittest.TestCase):
    def setUp(self):
        self.source = media_source.H500MediaSource(None)

    def test_a_folder_carries_its_poster(self):
        frame = Path("/media/Front/2026-08-13/174100.jpg")
        child = self.source._child("", "Front", 0, Path("/media/Front"), frame)
        self.assertEqual(child.thumbnail, f"/signed{frame}")

    def test_a_folder_with_no_poster_has_no_thumbnail(self):
        """signing does not check the file exists, so a None poster must not
        become a URL that 404s behind every folder."""
        child = self.source._child("", "Front", 0, Path("/media/Front"), None)
        self.assertIsNone(child.thumbnail)

    def test_a_clip_still_carries_its_own_frame(self):
        clip = Path("/media/Front/2026-08-13/174100.mp4")
        child = self.source._child("Front/2026-08-13", "174100.mp4", 2, clip)
        self.assertEqual(child.thumbnail,
                         "/signed/media/Front/2026-08-13/174100.jpg")

    def test_a_camera_folder_reads_its_underscores_as_spaces(self):
        child = self.source._child("", "front_doorbell", 0,
                                   Path("/media/front_doorbell"), None)
        self.assertEqual(child.title, "front doorbell")


class Wiring(unittest.TestCase):
    def test_the_poster_search_runs_off_the_event_loop(self):
        """It stats directories, once per browse."""
        self.assertIn(
            "async_add_executor_job(\n            _entries, path, suffixes)",
            SOURCE)


if __name__ == "__main__":
    unittest.main()
