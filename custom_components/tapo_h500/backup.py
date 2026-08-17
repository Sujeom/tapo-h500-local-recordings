"""Taking the typed-in state out and putting it back.

Face names and the camera layout are the only state here a hub cannot
reproduce. Recordings live on the hub, settings live on the hub, and every
sensor is derived from one of those. These two came out of somebody's head --
months of opening photographs to work out who a twelve-digit number is -- and
they live on the config entry, so deleting the entry takes them with it and
nothing warns first.

Pure functions, so the merge rules can be run for real rather than read. They
are where a restore quietly loses work.
"""
from __future__ import annotations

from .const import (
    BACKUP_VERSION, CONF_AUTO_DOWNLOAD, CONF_CAMERA_ORDER, CONF_CARD_DAYS,
    CONF_CONVERT_MP4, CONF_DOWNLOAD_TYPES, CONF_FACE_NAMES, CONF_KEEP_DOWNLOADS,
    CONF_KEEP_PERSON, CONF_KEEP_RINGS, CONF_NIGHT_END, CONF_NIGHT_START,
    CONF_SENSITIVITY, CONF_SILENT_HOURS,
)

# Every option somebody chose, as opposed to plumbing. The same argument that
# put the face names in the backup: a reinstall loses these and a hub cannot
# reproduce them. The poll interval stays out (it is about the connection,
# and the only option whose change reloads the entry), and credentials never
# ride in a file people paste into forums.
USER_OPTIONS = (
    CONF_AUTO_DOWNLOAD, CONF_CONVERT_MP4, CONF_DOWNLOAD_TYPES,
    CONF_KEEP_DOWNLOADS, CONF_KEEP_RINGS, CONF_KEEP_PERSON,
    CONF_NIGHT_START, CONF_NIGHT_END, CONF_SENSITIVITY, CONF_SILENT_HOURS,
    CONF_CARD_DAYS,
)


def snapshot(names: dict[str, str], ranks: dict[str, int],
             options: dict | None = None) -> dict:
    """What a backup contains, shaped to paste straight back in.

    The version is stamped now because it cannot be stamped later: by the time
    there is a second format, every file in the first one is already out there
    unlabelled.
    """
    return {
        "version": BACKUP_VERSION,
        "face_names": dict(names),
        "camera_order": dict(ranks),
        # Only the options that exist: absent stays absent, so restoring an
        # old backup does not overwrite a newer decision with a default.
        "settings": {key: options[key] for key in USER_OPTIONS
                     if key in (options or {})},
    }


def merge_names(current: dict[str, str], incoming: dict,
                replace: bool) -> dict[str, str]:
    """The name map after a restore.

    Merges unless asked to replace. The usual restore is an older backup onto
    an entry that has learned a few more names since, and replacing there
    discards them without saying so.

    A blank name removes rather than stores, which is the rule the card and
    the options screen already use -- so a backup with an emptied box
    round-trips as a removal instead of putting back an entry named "".
    """
    names = {} if replace else dict(current)
    for key, value in incoming.items():
        cleaned = str(value).strip()
        if cleaned:
            names[str(key)] = cleaned
        else:
            names.pop(str(key), None)
    return names


def merge_ranks(current: dict[str, int], incoming: dict | None,
                replace: bool) -> dict[str, int]:
    """The camera layout after a restore, or the current one if none was given.

    A backup taken before the layout existed carries no camera_order at all,
    and must not be read as "the layout is empty".
    """
    if incoming is None:
        return dict(current)
    ranks = {} if replace else dict(current)
    ranks.update({str(key): int(value) for key, value in incoming.items()})
    return ranks


def merge_settings(current: dict, incoming: dict | None) -> dict:
    """The authored options after a restore.

    None (a backup from before settings rode along) keeps everything as it
    is. Otherwise the backup's values win for exactly the keys it names,
    filtered to the known list so a hand-edited file cannot smuggle
    arbitrary keys into the entry's options.
    """
    merged = dict(current)
    if incoming:
        merged.update({key: value for key, value in incoming.items()
                       if key in USER_OPTIONS})
    return merged


def restored_options(options: dict, names: dict[str, str],
                     ranks: dict[str, int] | None,
                     settings: dict | None = None) -> dict:
    """The whole options mapping to write back.

    Whole, deliberately. Home Assistant replaces options wholesale on save, so
    writing only the names deleted the poll interval and everything else --
    which is a bug this integration has already shipped once.
    """
    updated = {**options, CONF_FACE_NAMES: names}
    if ranks is not None:
        updated[CONF_CAMERA_ORDER] = ranks
    if settings is not None:
        updated.update(settings)
    return updated
