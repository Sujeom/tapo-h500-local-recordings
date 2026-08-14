"""The day in one picture.

A doorbell produces dozens of near-identical fifteen-second clips a day, and
looking through them means opening dozens of things. A contact sheet is the
oldest answer to that problem: every frame at once, small, in order, so the
one that matters is found by looking rather than by clicking.

Built with ffmpeg, which is already a dependency and already makes every
thumbnail here. The obvious alternative is Pillow, and it would mean adding an
image library to the requirements to lay out pictures ffmpeg can already tile.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant

from .media import camera_dir

_LOGGER = logging.getLogger(__name__)

# Four across, and at most twenty-four. A busy street can produce hundreds of
# recordings in a day, and a sheet forty rows tall is not a summary of
# anything -- it is the same scrolling problem in a different shape.
COLUMNS = 4
MAX_TILES = 24
# Each tile. 320 wide is legible on a phone at four across and keeps the whole
# sheet around a couple of hundred kilobytes.
TILE_WIDTH = 320
TILE_HEIGHT = 180
# How long to let ffmpeg take. Two dozen small JPEGs is fast; a hung process
# would otherwise hold an executor thread for the life of the session.
TIMEOUT = 30


def grid(count: int, columns: int, cap: int) -> tuple[int, int, int]:
    """The shape to lay `count` pictures out in: (across, down, used).

    (0, 0, 0) for nothing at all, so a caller can tell "no sheet" from "an
    empty one".

    Never wider than there are pictures: three recordings in a four-wide grid
    leave a blank column that reads as a missing photograph.
    """
    used = max(0, min(count, cap))
    if used == 0:
        return (0, 0, 0)
    across = min(columns, used)
    down = -(-used // across)
    return (across, down, used)


def _thumbnails(directory: Path) -> list[Path]:
    """Every thumbnail in one day's folder, oldest first.

    Names are <HHMMSS>.jpg, so sorting them is sorting by time.
    """
    if not directory.is_dir():
        return []
    return sorted(item for item in directory.iterdir()
                  if item.is_file() and item.suffix == ".jpg")


def _stage(pictures: list[Path], into: Path) -> int:
    """Link the chosen frames in as 000.jpg, 001.jpg, ...

    ffmpeg's image2 demuxer reads a numbered sequence and stops at the first
    gap, so the names have to be contiguous from zero -- the real filenames
    are times of day and full of gaps. Symlinks rather than copies: this runs
    whenever a dashboard asks for the picture, and copying two dozen files
    each time to feed a read-only process is work for nothing.
    """
    for position, picture in enumerate(pictures):
        (into / f"{position:03d}.jpg").symlink_to(picture)
    return len(pictures)


async def async_contact_sheet(hass: HomeAssistant, camera,
                              day: str) -> bytes | None:
    """Every frame from one local day, tiled into a single JPEG.

    None when there is nothing to show -- a quiet day, or a day whose clips
    have not downloaded -- rather than a blank sheet, which would look like a
    fault.
    """
    folder = camera_dir(hass, camera) / day
    pictures = await hass.async_add_executor_job(_thumbnails, folder)
    across, down, used = grid(len(pictures), COLUMNS, MAX_TILES)
    if used == 0:
        return None
    # The newest, shown oldest-first within the sheet. Capping from the front
    # would fix the sheet on the morning and never show the evening.
    chosen = pictures[-used:]

    staging = await hass.async_add_executor_job(tempfile.mkdtemp)
    try:
        await hass.async_add_executor_job(_stage, chosen, Path(staging))
        binary = get_ffmpeg_manager(hass).binary
        process = await asyncio.create_subprocess_exec(
            binary, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-start_number", "0", "-i", f"{staging}/%03d.jpg",
            "-vf", (f"scale={TILE_WIDTH}:{TILE_HEIGHT}"
                    ":force_original_aspect_ratio=increase,"
                    f"crop={TILE_WIDTH}:{TILE_HEIGHT},"
                    f"tile={across}x{down}:margin=4:padding=2:color=black"),
            "-frames:v", "1", "-q:v", "4", "-f", "image2", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), TIMEOUT)
        except TimeoutError:
            process.kill()
            _LOGGER.warning("Contact sheet for %s timed out",
                            camera.get("alias"))
            return None
        if process.returncode != 0 or not out:
            _LOGGER.debug("Contact sheet failed: %s",
                          err.decode(errors="replace")[:300])
            return None
        return out
    finally:
        await hass.async_add_executor_job(
            shutil.rmtree, staging, True)
