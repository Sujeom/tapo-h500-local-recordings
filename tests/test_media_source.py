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


import asyncio
import json
import tempfile


class ByType(unittest.TestCase):
    """Virtual folders over the whole archive: presses, people, vehicles, pets.

    The cards filter the last day; this filters the month on disk, using the
    sidecars downloads leave behind. Identifiers stay real camera/date/file
    paths, so playing one goes through exactly the machinery a browsed clip
    does -- and the virtual ids never touch the filesystem resolver at all.
    """

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        # media_source bound media_root into its own namespace at import,
        # so that binding is the one to patch.
        self._old_root = media_source.media_root
        media_source.media_root = lambda hass: Path(self.root.name)
        self.addCleanup(setattr, media_source, "media_root", self._old_root)
        self.base = Path(self.root.name) / "tapo_h500"

    def _clip(self, camera, date, clock, types=None):
        day = self.base / camera / date
        day.mkdir(parents=True, exist_ok=True)
        (day / f"{clock}.mp4").write_bytes(b"v")
        (day / f"{clock}.jpg").write_bytes(b"t")
        if types is not None:
            (day / f"{clock}.json").write_text(
                json.dumps({"detection_types": types}))

    def _hass(self):
        class _Hass:
            async def async_add_executor_job(self, fn, *args):
                return fn(*args)
        return _Hass()

    def _browse(self, identifier):
        source = media_source.H500MediaSource(self._hass())
        item = types.SimpleNamespace(identifier=identifier)
        return asyncio.run(source.async_browse_media(item))

    def test_the_root_offers_the_type_folders(self):
        self._clip("front", "2026-08-17", "120000", [6])
        titles = [child.title for child in self._browse("").children]
        for wanted in ("Doorbell presses", "People", "Vehicles", "Pets"):
            self.assertIn(wanted, titles)

    def test_a_type_folder_lists_only_its_own(self):
        self._clip("front", "2026-08-17", "120000", [2, 6])
        self._clip("front", "2026-08-17", "130000", [2, 8])
        self._clip("side", "2026-08-16", "090000", [6, 17])
        found = self._browse("by-type/people").children
        self.assertEqual(
            sorted(child.identifier for child in found),
            ["front/2026-08-17/120000.mp4", "side/2026-08-16/090000.mp4"])

    def test_newest_first(self):
        self._clip("front", "2026-08-16", "090000", [6])
        self._clip("front", "2026-08-17", "120000", [6])
        found = self._browse("by-type/people").children
        self.assertEqual(found[0].identifier, "front/2026-08-17/120000.mp4")

    def test_an_unclassified_clip_is_not_listed(self):
        """Downloaded before sidecars existed: absent, not miscategorised."""
        self._clip("front", "2026-08-17", "120000")
        self.assertEqual(self._browse("by-type/people").children, [])

    def test_a_broken_sidecar_is_skipped_not_fatal(self):
        day = self.base / "front" / "2026-08-17"
        day.mkdir(parents=True)
        (day / "120000.mp4").write_bytes(b"v")
        (day / "120000.json").write_text("not json")
        self._clip("front", "2026-08-17", "130000", [6])
        found = self._browse("by-type/people").children
        self.assertEqual(len(found), 1)

    def test_the_names_say_camera_and_moment(self):
        self._clip("front_door", "2026-08-17", "120515", [6])
        child = self._browse("by-type/people").children[0]
        self.assertIn("front door", child.title)
        self.assertIn("12:05", child.title)

    def test_the_listing_is_capped(self):
        for minute in range(60):
            self._clip("front", "2026-08-17", f"12{minute:02d}00", [6])
        for minute in range(60):
            self._clip("front", "2026-08-16", f"12{minute:02d}00", [6])
        found = self._browse("by-type/people").children
        self.assertEqual(len(found), media_source.TYPE_LISTING_CAP)
        # ...and the cap drops the oldest: all sixty of the newer day
        # survive, and the cut lands in the older day's morning.
        identifiers = [child.identifier for child in found]
        self.assertEqual(
            sum("2026-08-17" in name for name in identifiers), 60)
        self.assertIn("front/2026-08-16/125900.mp4", identifiers)
        self.assertNotIn("front/2026-08-16/120000.mp4", identifiers)

    def test_an_unknown_type_slug_is_refused(self):
        with self.assertRaises(media_source.Unresolvable):
            self._browse("by-type/../../etc")

    def test_playing_one_uses_the_ordinary_resolver(self):
        """The identifiers are real paths, so resolve needs no new code."""
        self._clip("front", "2026-08-17", "120000", [6])
        child = self._browse("by-type/people").children[0]
        item = types.SimpleNamespace(identifier=child.identifier)
        source = media_source.H500MediaSource(self._hass())
        # The browser-stub PlayMedia takes no arguments; give the resolver a
        # real-shaped one for this test.
        self.addCleanup(setattr, media_source, "PlayMedia",
                        media_source.PlayMedia)
        media_source.PlayMedia = lambda url, mime: types.SimpleNamespace(
            url=url, mime_type=mime)
        resolved = asyncio.run(source.async_resolve_media(item))
        self.assertEqual(resolved.mime_type, "video/mp4")


class TodayFolder(unittest.TestCase):
    """One folder for "what happened today", cameras merged, newest first."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self._old_root = media_source.media_root
        media_source.media_root = lambda hass: Path(self.root.name)
        self.addCleanup(setattr, media_source, "media_root", self._old_root)
        self.base = Path(self.root.name) / "tapo_h500"
        # The harness clock is frozen; "today" is its local date.
        import datetime
        from homeassistant.util import dt as dt_util
        self.today = dt_util.as_local(dt_util.utc_from_timestamp(
            int(dt_util.utcnow().timestamp()))).strftime("%Y-%m-%d")
        self.yesterday = (datetime.date.fromisoformat(self.today)
                          - datetime.timedelta(days=1)).isoformat()

    def _clip(self, camera, date, clock):
        day = self.base / camera / date
        day.mkdir(parents=True, exist_ok=True)
        (day / f"{clock}.mp4").write_bytes(b"v")
        (day / f"{clock}.jpg").write_bytes(b"t")

    def _hass(self):
        class _Hass:
            async def async_add_executor_job(self, fn, *args):
                return fn(*args)
        return _Hass()

    def _browse(self, identifier):
        source = media_source.H500MediaSource(self._hass())
        item = types.SimpleNamespace(identifier=identifier)
        return asyncio.run(source.async_browse_media(item))

    def test_the_root_offers_it_first(self):
        self._clip("front", self.today, "120000")
        children = self._browse("").children
        self.assertEqual(children[-5].title, "Today",
                         "Today leads the virtual folders")

    def test_it_merges_cameras_newest_first_today_only(self):
        self._clip("front", self.today, "090000")
        self._clip("side", self.today, "110000")
        self._clip("front", self.yesterday, "230000")
        found = self._browse("today").children
        self.assertEqual([child.identifier for child in found],
                         [f"side/{self.today}/110000.mp4",
                          f"front/{self.today}/090000.mp4"])

    def test_a_quiet_day_is_an_empty_folder_not_an_error(self):
        self._clip("front", self.yesterday, "230000")
        self.assertEqual(self._browse("today").children, [])
