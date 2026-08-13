"""Filesystem and ffmpeg side of the H500 integration.

Clips live at ``<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.<ext>`` with a
matching ``.jpg`` thumbnail. The layout is derived from the clip's start time,
so "is this already downloaded?" is a path check rather than a stored index.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import timedelta
from pathlib import Path

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .clips import camera_slug, surplus
from .const import (
    CONVERT_ARGS, MEDIA_DIR, PREVIEW_MAX_BYTES, PREVIEW_SECONDS, THUMBNAIL_ARGS,
)

_LOGGER = logging.getLogger(__name__)

URL_LIFETIME = timedelta(hours=12)


def media_root(hass: HomeAssistant) -> Path:
    try:
        return Path(hass.config.media_dirs["local"]).resolve()
    except KeyError as err:
        raise HomeAssistantError(
            "Tapo H500 requires a Home Assistant media directory named 'local'"
        ) from err


def camera_dir(hass: HomeAssistant, camera) -> Path:
    return media_root(hass) / MEDIA_DIR / camera_slug(camera)


def clip_path(hass: HomeAssistant, camera, start_time: int, suffix: str) -> Path:
    moment = dt_util.as_local(dt_util.utc_from_timestamp(int(start_time)))
    path = (camera_dir(hass, camera) / moment.strftime("%Y-%m-%d")
            / f"{moment.strftime('%H%M%S')}{suffix}")
    root = media_root(hass)
    # camera_slug and strftime cannot produce traversal, but the whole point of
    # a trust boundary is not taking that on faith.
    if not path.is_relative_to(root):
        raise HomeAssistantError("Refusing to write outside the media directory")
    return path


def relative(hass: HomeAssistant, path: Path) -> str:
    return path.relative_to(media_root(hass)).as_posix()


def media_content_id(hass: HomeAssistant, path: Path) -> str:
    return f"media-source://media_source/local/{relative(hass, path)}"


def signed_url(hass: HomeAssistant, path: Path) -> str:
    """A URL the dashboard can put straight into <img> or <video>."""
    return async_sign_path(
        hass, f"/media/local/{relative(hass, path)}", URL_LIFETIME)


def existing_clip(hass: HomeAssistant, camera, start_time: int) -> Path | None:
    for suffix in (".mp4", ".ts"):
        path = clip_path(hass, camera, start_time, suffix)
        if path.exists():
            return path
    return None


def describe(hass: HomeAssistant, path: Path) -> dict:
    """Playable URLs for one downloaded clip. No filesystem access.

    The thumbnail URL is emitted unconditionally; if ffmpeg failed to make one
    it 404s, which callers render as a missing image rather than an error.
    """
    return {
        "media_content_id": media_content_id(hass, path),
        "path": relative(hass, path),
        "url": signed_url(hass, path),
        "thumbnail": signed_url(hass, path.with_suffix(".jpg")),
    }


def scan_downloaded(hass: HomeAssistant, camera, starts) -> dict[int, Path]:
    """Which of these clip start times are already on disk. Blocking."""
    found = {}
    for start_time in starts:
        path = existing_clip(hass, camera, start_time)
        if path is not None:
            found[start_time] = path
    return found


async def _run_ffmpeg(hass: HomeAssistant, args: list[str]) -> bool:
    binary = get_ffmpeg_manager(hass).binary
    process = await asyncio.create_subprocess_exec(
        binary, "-hide_banner", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, error = await process.communicate()
    if process.returncode:
        _LOGGER.debug("ffmpeg %s failed: %s", args, error.decode(errors="replace"))
    return process.returncode == 0


def _make_temp(parent: Path, suffix: str) -> tuple[int, Path]:
    parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".tapo-h500-", suffix=suffix, dir=parent)
    return descriptor, Path(name)


async def async_thumbnail(hass: HomeAssistant, video: Path) -> Path | None:
    """One JPEG per clip, one second in so the frame is not a black lead-in."""
    thumbnail = video.with_suffix(".jpg")
    for seek in ("1", "0"):
        made = await _run_ffmpeg(hass, [
            "-y", "-ss", seek, "-i", str(video),
            *THUMBNAIL_ARGS, str(thumbnail),
        ])
        if made and await hass.async_add_executor_job(_has_content, thumbnail):
            return thumbnail
    return None


def _has_content(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


async def async_preview_clip(
    hass: HomeAssistant, client, camera, start_time: int
) -> Path | None:
    """A thumbnail for a clip that is still only on the hub.

    The download session takes a time window, so a preview does not need the
    whole recording — a couple of seconds is enough for one decodable frame,
    which costs a fraction of a full clip. Abandoning the stream early is
    deliberate: closing the generator unwinds the media session cleanly.

    Cached at exactly the path the downloaded clip's thumbnail would use, so
    downloading later finds it already there, and deleting the clip removes it.
    """
    thumbnail = clip_path(hass, camera, start_time, ".jpg")
    if await hass.async_add_executor_job(_has_content, thumbnail):
        return thumbnail
    descriptor, temporary = await hass.async_add_executor_job(
        _make_temp, thumbnail.parent, ".ts.part")
    stream = os.fdopen(descriptor, "wb")
    received = 0
    try:
        async for chunk in client.iter_recording(
                camera, start_time, start_time + PREVIEW_SECONDS):
            received += len(chunk)
            await hass.async_add_executor_job(stream.write, chunk)
            if received >= PREVIEW_MAX_BYTES:
                break
        await hass.async_add_executor_job(stream.close)
        stream = None
        if not received:
            return None
        # No -ss seek: only the opening seconds were fetched, so the first
        # decodable frame is all there is.
        made = await _run_ffmpeg(hass, [
            "-y", "-f", "mpegts", "-i", str(temporary),
            *THUMBNAIL_ARGS, str(thumbnail),
        ])
        if made and await hass.async_add_executor_job(_has_content, thumbnail):
            return thumbnail
        return None
    except Exception as err:
        # A preview is decoration. A hub that will not serve one must not turn
        # into a broken recording list.
        _LOGGER.debug("Preview for clip %s failed: %s", start_time, err)
        return None
    finally:
        if stream is not None:
            await hass.async_add_executor_job(stream.close)
        await hass.async_add_executor_job(temporary.unlink, True)


async def async_download_clip(
    hass: HomeAssistant, client, camera, start_time: int, end_time: int,
    convert: bool = True,
) -> dict:
    """Stream one indexed clip to disk, then remux and thumbnail it."""
    target = clip_path(hass, camera, start_time, ".mp4" if convert else ".ts")
    descriptor, temporary = await hass.async_add_executor_job(
        _make_temp, target.parent, ".ts.part")
    remuxed: Path | None = None
    stream = os.fdopen(descriptor, "wb")
    received = 0
    try:
        async for chunk in client.iter_recording(camera, start_time, end_time):
            received += len(chunk)
            await hass.async_add_executor_job(stream.write, chunk)
        if received == 0:
            raise HomeAssistantError("H500 returned no video data")
        await hass.async_add_executor_job(stream.close)
        stream = None
        if convert:
            descriptor, remuxed = await hass.async_add_executor_job(
                _make_temp, target.parent, ".mp4.part")
            os.close(descriptor)
            if not await _run_ffmpeg(hass, [
                "-y", "-f", "mpegts", "-i", str(temporary),
                *CONVERT_ARGS, str(remuxed),
            ]):
                raise HomeAssistantError("Could not convert the clip to MP4")
            await hass.async_add_executor_job(os.replace, remuxed, target)
            remuxed = None
        else:
            await hass.async_add_executor_job(os.replace, temporary, target)
    except Exception as err:
        if isinstance(err, HomeAssistantError):
            raise
        raise HomeAssistantError("H500 recording download did not complete") from err
    finally:
        if stream is not None:
            await hass.async_add_executor_job(stream.close)
        await hass.async_add_executor_job(temporary.unlink, True)
        if remuxed is not None:
            await hass.async_add_executor_job(remuxed.unlink, True)
    await async_thumbnail(hass, target)
    return {**describe(hass, target), "bytes": received}


def _delete(paths: list[Path]) -> list[Path]:
    removed = []
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(path)
    for parent in {path.parent for path in removed}:
        try:
            parent.rmdir()
        except OSError:
            pass
    return removed


async def async_delete_clip(hass: HomeAssistant, camera, start_time: int) -> list[str]:
    """Remove the downloaded copy of a clip. The hub keeps its own."""
    candidates = [
        clip_path(hass, camera, start_time, suffix)
        for suffix in (".mp4", ".ts", ".jpg")
    ]
    removed = await hass.async_add_executor_job(_delete, candidates)
    return [relative(hass, path) for path in removed]


def _videos(directory: Path) -> list[Path]:
    # Names sort chronologically: <date>/<HHMMSS>.<ext>.
    return sorted(path for path in directory.glob("*/*")
                  if path.suffix in (".mp4", ".ts"))


async def async_prune(hass: HomeAssistant, camera, keep: int) -> list[str]:
    """Drop the oldest downloads once a camera holds more than `keep`."""
    if keep <= 0:
        return []
    videos = await hass.async_add_executor_job(_videos, camera_dir(hass, camera))
    doomed = surplus(videos, keep)
    if not doomed:
        return []
    paths = [path for video in doomed
             for path in (video, video.with_suffix(".jpg"))]
    removed = await hass.async_add_executor_job(_delete, paths)
    return [relative(hass, path) for path in removed]


def _newest_thumbnail(directory: Path) -> bytes | None:
    # Names sort chronologically, so the last path is the newest clip.
    thumbnails = sorted(directory.glob("*/*.jpg"))
    return thumbnails[-1].read_bytes() if thumbnails else None


async def async_latest_image(hass: HomeAssistant, camera) -> bytes | None:
    return await hass.async_add_executor_job(
        _newest_thumbnail, camera_dir(hass, camera))
