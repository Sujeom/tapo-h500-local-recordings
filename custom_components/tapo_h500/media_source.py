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
        names = await self.hass.async_add_executor_job(_listing, path, suffixes)
        return BrowseMediaSource(
            domain=DOMAIN, identifier=identifier or None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=identifier.split("/")[-1] if identifier else "Tapo H500",
            can_play=False, can_expand=True,
            children_media_class=MediaClass.VIDEO if depth == 2
            else MediaClass.DIRECTORY,
            children=[self._child(identifier, name, depth, path / name)
                      for name in names],
        )

    def _child(self, parent: str, name: str, depth: int, path: Path
               ) -> BrowseMediaSource:
        identifier = f"{parent}/{name}" if parent else name
        if depth < 2:
            return BrowseMediaSource(
                domain=DOMAIN, identifier=identifier,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=name.replace("_", " ") if depth == 0 else name,
                can_play=False, can_expand=True,
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
