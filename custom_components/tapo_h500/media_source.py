"""Browse downloaded H500 clips under Media, by camera and date."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource, MediaSource, MediaSourceItem, PlayMedia, Unresolvable,
)
from homeassistant.core import HomeAssistant

import json

from .const import DOMAIN, MEDIA_DIR
from .media import media_root, signed_url

MIME_TYPES = {".mp4": "video/mp4", ".ts": "video/mp2t"}

# Virtual folders over the whole archive, fed by the JSON sidecars downloads
# write. The cards filter the last day; these filter the month on disk. Four
# only, and the four people actually go looking for -- the rest of the codes
# are riders (motion accompanies everything, faces never appear without the
# person code beside them).
TYPE_FOLDERS = (
    ("presses", "Doorbell presses", 17),
    ("people", "People", 6),
    ("vehicles", "Vehicles", 8),
    ("pets", "Pets", 9),
)
TYPE_PREFIX = "by-type"

# Enough to answer "what has been through here lately" without handing the
# frontend a thousand tiles. Newest first, so the cap drops the oldest.
TYPE_LISTING_CAP = 100


def _typed(root: Path, code: int, cap: int) -> list[Path]:
    """Every downloaded clip whose sidecar lists this detection code.

    Newest first. Walks day folders in reverse-lexical order (which is
    reverse-chronological, the layout guarantees it) and stops at the cap,
    so a month of archive is not read end to end for the first page.
    """
    found: list[Path] = []
    if not root.is_dir():
        return found
    days = sorted(
        ((camera, day)
         for camera in root.iterdir() if camera.is_dir()
         for day in camera.iterdir() if day.is_dir()),
        key=lambda pair: pair[1].name, reverse=True)
    for _, day in days:
        if len(found) >= cap:
            break
        for sidecar in sorted(day.glob("*.json"), reverse=True):
            try:
                types = json.loads(sidecar.read_text()).get(
                    "detection_types", [])
            except (OSError, ValueError):
                continue
            if code not in types:
                continue
            for suffix in (".mp4", ".ts"):
                video = sidecar.with_suffix(suffix)
                if video.exists():
                    found.append(video)
                    break
            if len(found) >= cap:
                break
    found.sort(key=lambda path: (path.parent.name, path.name), reverse=True)
    return found[:cap]


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    return H500MediaSource(hass)


def _listing(directory: Path, suffixes: tuple[str, ...] | None) -> list[str]:
    if not directory.is_dir():
        return []
    if suffixes is None:
        names = [item.name for item in directory.iterdir() if item.is_dir()]
    else:
        names = [item.name for item in directory.iterdir()
                 if item.is_file() and item.suffix in suffixes]
    return sorted(names)


def _poster(directory: Path) -> Path | None:
    """The newest thumbnail under here, for a folder to show as its cover.

    Clips already carry their own frame; the folders above them were blank
    tiles, which is a poor way to find yesterday afternoon among thirty
    identical grey rectangles.

    Paths are <camera>/<YYYY-MM-DD>/<HHMMSS>.jpg, so lexical order is
    chronological order and the newest is the last of the last. That means one
    iterdir per level rather than walking the whole tree -- a camera with a
    month of recordings holds thousands of files, and this runs on every
    browse.

    It walks back through earlier days if the newest has no thumbnail at all,
    which happens for clips downloaded before thumbnails existed or where the
    conversion failed. Bounded by the number of day folders and normally
    finished on the first.
    """
    if not directory.is_dir():
        return None
    days = sorted(item for item in directory.iterdir() if item.is_dir())
    for day in reversed(days):
        found = _poster(day)
        if found is not None:
            return found
    thumbs = sorted(item for item in directory.iterdir()
                    if item.is_file() and item.suffix == ".jpg")
    return thumbs[-1] if thumbs else None


def _entries(directory: Path,
             suffixes: tuple[str, ...] | None) -> list[tuple[str, Path | None]]:
    """Each child's name, and for a folder the thumbnail it should show.

    One executor job for both. Splitting them would put a second round of
    blocking filesystem work on the event loop, once per browse.
    """
    names = _listing(directory, suffixes)
    if suffixes is not None:
        # A clip's own frame sits beside it and needs no searching.
        return [(name, None) for name in names]
    return [(name, _poster(directory / name)) for name in names]


class H500MediaSource(MediaSource):
    """Identifiers are ``camera``, ``camera/date`` or ``camera/date/file``."""

    name = "Tapo H500"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _path(self, identifier: str) -> Path:
        root = media_root(self.hass) / MEDIA_DIR
        parts = [part for part in identifier.split("/") if part]
        if any(part in ("..", ".") or "\\" in part for part in parts):
            raise Unresolvable("Invalid media identifier")
        path = root.joinpath(*parts).resolve()
        if not path.is_relative_to(root.resolve()):
            raise Unresolvable("Invalid media identifier")
        return path

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        path = self._path(item.identifier)
        mime = MIME_TYPES.get(path.suffix)
        if mime is None or not await self.hass.async_add_executor_job(path.is_file):
            raise Unresolvable(f"Not a downloaded H500 clip: {item.identifier}")
        return PlayMedia(signed_url(self.hass, path), mime)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        identifier = item.identifier or ""
        if identifier.split("/", 1)[0] == TYPE_PREFIX:
            return await self._browse_type(identifier)
        depth = len([part for part in identifier.split("/") if part])
        if depth >= 3:
            raise Unresolvable("H500 clips cannot be expanded")
        path = self._path(identifier)
        suffixes = tuple(MIME_TYPES) if depth == 2 else None
        children = await self.hass.async_add_executor_job(
            _entries, path, suffixes)
        return BrowseMediaSource(
            domain=DOMAIN, identifier=identifier or None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=identifier.split("/")[-1] if identifier else "Tapo H500",
            can_play=False, can_expand=True,
            children_media_class=MediaClass.VIDEO if depth == 2
            else MediaClass.DIRECTORY,
            children=[self._child(identifier, name, depth, path / name, poster)
                      for name, poster in children]
            + ([self._type_folder(slug, title)
                for slug, title, _ in TYPE_FOLDERS] if depth == 0 else []),
        )

    def _type_folder(self, slug: str, title: str) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN, identifier=f"{TYPE_PREFIX}/{slug}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=title, can_play=False, can_expand=True,
        )

    async def _browse_type(self, identifier: str) -> BrowseMediaSource:
        """One virtual folder: the archive filtered by a detection code.

        The children carry ordinary camera/date/file identifiers, so playing
        one goes through exactly the machinery a browsed clip does -- and
        these virtual ids never reach the filesystem resolver at all.
        """
        slug = identifier.split("/", 1)[1] if "/" in identifier else ""
        matching = [entry for entry in TYPE_FOLDERS if entry[0] == slug]
        if not matching:
            raise Unresolvable("Unknown recording type")
        slug, title, code = matching[0]
        root = media_root(self.hass) / MEDIA_DIR
        videos = await self.hass.async_add_executor_job(
            _typed, root, code, TYPE_LISTING_CAP)
        return BrowseMediaSource(
            domain=DOMAIN, identifier=identifier,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=title, can_play=False, can_expand=True,
            children_media_class=MediaClass.VIDEO,
            children=[self._typed_child(root, video) for video in videos],
        )

    def _typed_child(self, root: Path, video: Path) -> BrowseMediaSource:
        camera, day, name = video.relative_to(root).parts
        clock = video.stem
        when = (f"{clock[:2]}:{clock[2:4]}"
                if len(clock) == 6 and clock.isdigit() else clock)
        return BrowseMediaSource(
            domain=DOMAIN, identifier=f"{camera}/{day}/{name}",
            media_class=MediaClass.VIDEO, media_content_type=MediaType.VIDEO,
            # Which door and when, because a flat list spans cameras and days.
            title=f"{camera.replace('_', ' ')} · {day} {when}",
            can_play=True, can_expand=False,
            thumbnail=signed_url(self.hass, video.with_suffix(".jpg")),
        )

    def _child(self, parent: str, name: str, depth: int, path: Path,
               poster: Path | None = None) -> BrowseMediaSource:
        identifier = f"{parent}/{name}" if parent else name
        if depth < 2:
            return BrowseMediaSource(
                domain=DOMAIN, identifier=identifier,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=name.replace("_", " ") if depth == 0 else name,
                can_play=False, can_expand=True,
                # The newest frame under this folder, so a month of days is
                # something to look through rather than a grid of grey tiles.
                thumbnail=(signed_url(self.hass, poster)
                           if poster is not None else None),
            )
        clock = path.stem
        return BrowseMediaSource(
            domain=DOMAIN, identifier=identifier,
            media_class=MediaClass.VIDEO, media_content_type=MediaType.VIDEO,
            title=f"{clock[:2]}:{clock[2:4]}:{clock[4:6]}"
            if len(clock) == 6 and clock.isdigit() else clock,
            can_play=True, can_expand=False,
            thumbnail=signed_url(self.hass, path.with_suffix(".jpg")),
        )
