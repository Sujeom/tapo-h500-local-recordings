"""Browse downloaded H500 clips under Media, by camera and date."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource, MediaSource, MediaSourceItem, PlayMedia, Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, MEDIA_DIR
from .media import media_root, signed_url

MIME_TYPES = {".mp4": "video/mp4", ".ts": "video/mp2t"}


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
                      for name, poster in children],
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
