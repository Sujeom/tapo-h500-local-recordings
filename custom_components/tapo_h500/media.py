"""Filesystem and ffmpeg side of the H500 integration.

Clips live at ``<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.<ext>`` with a
matching ``.jpg`` thumbnail. The layout is derived from the clip's start time,
so "is this already downloaded?" is a path check rather than a stored index.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .clips import camera_slug, surplus
from .const import (
    CONVERT_ARGS, MEDIA_DIR, PREVIEW_KEEP, PREVIEW_MAX_BYTES, PREVIEW_SECONDS,
    THUMBNAIL_ARGS,
)

_LOGGER = logging.getLogger(__name__)


class EmptyRecordingError(HomeAssistantError):
    """A media session that completed cleanly and carried no video.

    Seen on 2026-08-18: the hub answered every session -- handshake, auth,
    protocol, a clean finished -- with zero bytes, for every clip of every
    age, until a reboot. Its own error type because the coordinator counts
    exactly this shape: it is hub state, not a bad clip.
    """

URL_LIFETIME = timedelta(hours=12)

# Appended to the second pass through a repeated daylight-saving hour.
# A letter, so it can never be confused with a digit of the clock, and
# one that sorts after nothing so the newest file stays last.
AMBIGUOUS_HOUR = "b"


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
    name = moment.strftime("%H%M%S")
    # The hour that repeats when daylight saving ends maps two different
    # recordings onto one wall clock, and without this the second silently
    # overwrites the first -- once a year, discovered only by needing the
    # footage that is no longer there.
    #
    # `fold` is 1 only on that second pass, so every other recording keeps the
    # name it has always had and nothing already on disk is orphaned. Sorting
    # inside that one hour is no longer chronological, which the contact sheet
    # shows and nothing else reads; losing a recording is the worse trade.
    if moment.fold:
        name += AMBIGUOUS_HOUR
    path = (camera_dir(hass, camera) / moment.strftime("%Y-%m-%d")
            / f"{name}{suffix}")
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
    which costs a fraction of a full clip.

    Those seconds are then read to the end even once there is enough for a
    frame. Closing the socket is not the same thing as finishing a session:
    the hub regards one as over when it has sent its own ``finished``
    notification, and dropping out of the loop early denies it that. It also
    leaves the client's media lock held by an abandoned generator until the
    event loop finalises it. The window is already bounded to
    ``PREVIEW_SECONDS``, so draining it costs a couple of seconds of video the
    hub was sending anyway, and only the opening bytes reach the disk.

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
                camera, start_time, start_time + PREVIEW_SECONDS,
                kind="preview"):
            # Keep reading past the cap; only stop writing. The tail is
            # discarded rather than stored, but it is still consumed so the
            # session reaches the hub's own end of it.
            if received < PREVIEW_MAX_BYTES:
                received += len(chunk)
                await hass.async_add_executor_job(stream.write, chunk)
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
    convert: bool = True, detected: list[int] | None = None,
    faces: list[int] | None = None,
) -> dict:
    """Stream one indexed clip to disk, then remux and thumbnail it.

    `detected` is what triggered the recording, when the caller knows. It is
    written to a JSON sidecar beside the clip, because the hub's own index
    only reaches back a day and download time is the one moment the
    classification exists -- anything that wants "the clips with a person in
    them" a week later reads it from there.
    """
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
            raise EmptyRecordingError("H500 returned no video data")
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
        if detected:
            payload = {"detection_types": list(detected)}
            if faces:
                # The only place "when did Alice last come" can be answered
                # from beyond the hub's one-day index.
                payload["face_ids"] = list(faces)
            await hass.async_add_executor_job(
                target.with_suffix(".json").write_text, json.dumps(payload))
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
        for suffix in (".mp4", ".ts", ".jpg", ".json")
    ]
    removed = await hass.async_add_executor_job(_delete, candidates)
    return [relative(hass, path) for path in removed]


def _videos(directory: Path) -> list[Path]:
    # Names sort chronologically: <date>/<HHMMSS>.<ext>.
    return sorted(path for path in directory.glob("*/*")
                  if path.suffix in (".mp4", ".ts"))


def _strays(directory: Path) -> list[Path]:
    """Thumbnails with no clip beside them, oldest first.

    No is-directory check, the same as `_videos`: glob over a camera that has
    recorded nothing yields nothing rather than raising.
    """
    return sorted(path for path in directory.glob("*/*.jpg")
                  if not path.with_suffix(".mp4").exists()
                  and not path.with_suffix(".ts").exists())


async def async_prune_previews(hass: HomeAssistant, camera) -> list[str]:
    """Hold a camera's stray preview frames to `PREVIEW_KEEP`.

    A stray is a thumbnail whose clip was never downloaded, which is most of
    them: previews exist precisely so the picture shows an event that is only
    on the hub. `async_prune` walks videos and deletes each one's thumbnail
    with it, so it never sees these -- they are one file per event, kept for
    the life of the installation.

    Not the retention number, which defaults to keeping everything and is
    about recordings. This is a cache of single frames with a ceiling high
    enough that it cannot evict one somebody could still be looking at; the
    newest always survives, which is the one the camera entity serves.
    """
    strays = await hass.async_add_executor_job(
        _strays, camera_dir(hass, camera))
    doomed = surplus(strays, PREVIEW_KEEP)
    if not doomed:
        return []
    removed = await hass.async_add_executor_job(_delete, doomed)
    return [relative(hass, path) for path in removed]


async def async_export(hass: HomeAssistant, camera, start_time: int,
                       destination: str) -> dict:
    """Copy a downloaded clip and its thumbnail somewhere retention cannot reach.

    Retention deletes and nothing archives, so the only copy of anything worth
    keeping lives where a busy week will evict it. This copies rather than
    moves: the media directory stays the working set, and an export that
    emptied it would break every card pointing at it.

    The destination has to be allowed by Home Assistant. Writing anywhere the
    process can reach would let a service call reach the whole filesystem.
    """
    source = existing_clip(hass, camera, start_time)
    if source is None:
        raise HomeAssistantError(
            "That recording has not been downloaded, so there is nothing to "
            "export. Download it first.")
    if not hass.config.is_allowed_path(destination):
        raise HomeAssistantError(
            f"{destination} is not an allowed directory. Add it to "
            "allowlist_external_dirs in configuration.yaml.")

    def _copy() -> list[str]:
        # camera_slug is what the media directory already uses, so an
        # export mirrors the layout people are used to browsing.
        target = Path(destination) / camera_slug(camera) / source.parent.name
        target.mkdir(parents=True, exist_ok=True)
        written = []
        for suffix in (source.suffix, ".jpg"):
            origin = source.with_suffix(suffix)
            if origin.is_file():
                shutil.copy2(origin, target / origin.name)
                written.append(str(target / origin.name))
        return written

    copied = await hass.async_add_executor_job(_copy)
    return {"exported": copied, "count": len(copied)}


async def async_verify(hass: HomeAssistant, path: Path) -> bool:
    """Whether a downloaded clip decodes.

    Checked while the hub still holds the original. A truncated download looks
    exactly like a good one on disk -- right name, plausible size -- and the
    only moment it can be fetched again is before retention evicts the source.
    Discovering it later means discovering it is gone.

    ffmpeg is used rather than ffprobe: it is the binary Home Assistant already
    manages, and decoding to nothing proves more than reading a header does.
    """
    return await _run_ffmpeg(hass, [
        "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-",
    ])


async def async_prune(hass: HomeAssistant, camera, keep: int,
                      protected: set[int] | None = None) -> list[str]:
    """Drop the oldest downloads once a camera holds more than `keep`.

    `protected` is a set of clip start times to leave alone however old they
    are -- doorbell presses, in practice. One retention number for everything
    meant a busy afternoon of motion could evict the press that was the whole
    reason for keeping anything, and it would go silently.
    """
    if keep <= 0:
        return []
    videos = await hass.async_add_executor_job(_videos, camera_dir(hass, camera))
    if protected:
        videos = [video for video in videos
                  if _start_from_path(video) not in protected]
    doomed = surplus(videos, keep)
    if not doomed:
        return []
    paths = [path for video in doomed
             for path in (video, video.with_suffix(".jpg"),
                          video.with_suffix(".json"))]
    removed = await hass.async_add_executor_job(_delete, paths)
    return [relative(hass, path) for path in removed]


async def async_classify_downloads(hass: HomeAssistant, client, camera,
                                   days: int) -> dict:
    """Write missing sidecars for on-disk clips, from the hub's detection log.

    Clips downloaded before sidecars existed appear in no type folder. The
    hub's log still remembers what triggered them -- it answers for month-old
    windows -- so this walks the archive, asks once per camera-day that has
    an unclassified clip, and writes the files the download would have. A
    day already covered costs the hub nothing, so re-running is cheap.

    A clip the log does not know stays unclassified: absent means absent,
    never guessed. Matching allows the same one second of index drift the
    detection-to-clip attachment does.
    """
    from datetime import datetime, time, timedelta

    from .clips import detection_types, face_ids

    def _scan() -> dict:
        directory = camera_dir(hass, camera)
        if not directory.is_dir():
            return {"scanned": 0, "written": 0, "days_queried": 0}
        # Via the timestamp, which is the one thing every dt provider --
        # including the test harness's frozen clock -- can produce.
        now_local = dt_util.as_local(dt_util.utc_from_timestamp(
            int(dt_util.utcnow().timestamp())))
        cutoff = now_local.date() - timedelta(days=max(1, days))
        scanned = written = queried = 0
        for day_dir in sorted(item for item in directory.iterdir()
                              if item.is_dir()):
            try:
                day = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                continue
            videos = [item for item in day_dir.iterdir()
                      if item.suffix in (".mp4", ".ts")]
            scanned += len(videos)
            missing = [video for video in videos
                       if not video.with_suffix(".json").exists()
                       and _start_from_path(video) is not None]
            if not missing:
                continue
            # The folder name is a LOCAL date, so the window asked of the
            # hub is that local day's epochs -- a UTC-day window would
            # silently miss every evening clip.
            low = int(datetime.combine(
                day, time(), tzinfo=now_local.tzinfo).timestamp())
            detections = client.detections(camera, low, low + 86400 - 1)
            queried += 1
            if detections is None:
                continue
            by_start = {record.get("start_time"): record
                        for record in detections
                        if record.get("start_time") is not None}
            for video in missing:
                start = _start_from_path(video)
                record = (by_start.get(start) or by_start.get(start + 1)
                          or by_start.get(start - 1))
                if record is None:
                    continue
                codes = detection_types(record)
                if not codes:
                    continue
                payload = {"detection_types": codes}
                recognised = face_ids(record)
                if recognised:
                    payload["face_ids"] = recognised
                video.with_suffix(".json").write_text(json.dumps(payload))
                written += 1
        return {"scanned": scanned, "written": written,
                "days_queried": queried}

    return await hass.async_add_executor_job(_scan)


def archive_face_search(hass: HomeAssistant, camera,
                        wanted: set[str]) -> list[dict]:
    """Every downloaded clip whose sidecar names one of these faces.

    Newest first, whole archive: the hub's own index reaches back a day, and
    this is the other eleven months. Blocking; callers run it in an
    executor. Only clips whose sidecar carries face ids can match --
    absent means absent, exactly as the type folders treat it.
    """
    found = []
    directory = camera_dir(hass, camera)
    if not directory.is_dir():
        return found
    for sidecar in directory.glob("*/*.json"):
        try:
            record = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        faces = {str(face) for face in record.get("face_ids") or []}
        if not (wanted & faces):
            continue
        for suffix in (".mp4", ".ts"):
            video = sidecar.with_suffix(suffix)
            if video.exists():
                start = _start_from_path(video)
                found.append({
                    "start_time": start,
                    "path": video,
                    "detection_types": record.get("detection_types") or [],
                })
                break
    found.sort(key=lambda entry: entry["start_time"] or 0, reverse=True)
    return found


def _start_from_path(path: Path) -> int | None:
    """The clip start time a download's own filename encodes.

    Paths are <camera>/<YYYY-MM-DD>/<HHMMSS>.mp4 in local time, which is what
    makes "already downloaded" a path check. Reading the time back out of the
    name avoids a second index that could disagree with the files on disk.
    """
    stem, fold = path.stem, 0
    if stem.endswith(AMBIGUOUS_HOUR) and stem[:-len(AMBIGUOUS_HOUR)].isdigit():
        # The daylight-saving second pass; see clip_path. Read back with the
        # same fold it was written with, or the roundtrip returns the FIRST
        # pass's instant and "already downloaded" answers for the wrong clip.
        stem, fold = stem[:-len(AMBIGUOUS_HOUR)], 1
    try:
        stamp = datetime.strptime(f"{path.parent.name} {stem}",
                                  "%Y-%m-%d %H%M%S").replace(fold=fold)
    except (ValueError, TypeError):
        return None
    # Through dt_util's zone, not the process's. clip_path writes these
    # names in Home Assistant's configured zone; reading them back through
    # the machine zone only works because HA sets TZ to match, and a
    # roundtrip that leans on a deployment coincidence is a roundtrip that
    # breaks in every other harness.
    zone = dt_util.as_local(dt_util.utc_from_timestamp(0)).tzinfo
    return int(stamp.replace(tzinfo=zone).timestamp())


def _newest_thumbnail(directory: Path) -> bytes | None:
    # Names sort chronologically, so the last path is the newest clip.
    thumbnails = sorted(directory.glob("*/*.jpg"))
    return thumbnails[-1].read_bytes() if thumbnails else None


async def async_latest_image(hass: HomeAssistant, camera) -> bytes | None:
    return await hass.async_add_executor_job(
        _newest_thumbnail, camera_dir(hass, camera))
