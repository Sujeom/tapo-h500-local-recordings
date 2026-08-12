"""Constants for Tapo H500."""

DOMAIN = "tapo_h500"
CONF_CLOUD_PASSWORD = "cloud_password"
DATA_HUBS = "hubs"
DATA_CARD = "card_registered"

SERVICE_LIST_RECORDINGS = "list_recordings"
SERVICE_DOWNLOAD_RECORDING = "download_recording"
SERVICE_DELETE_RECORDING = "delete_recording"
SERVICE_FORMAT_HUB_STORAGE = "format_hub_storage"

# Subdirectory of Home Assistant's "local" media directory that holds clips.
MEDIA_DIR = "tapo_h500"
CARD_URL = "/tapo_h500_static/tapo-h500-card.js"

CONF_POLL_INTERVAL = "poll_interval"
CONF_AUTO_DOWNLOAD = "auto_download"
CONF_CONVERT_MP4 = "convert_mp4"

AUTO_DOWNLOAD_OFF = "off"
AUTO_DOWNLOAD_RINGS = "rings"
AUTO_DOWNLOAD_ALL = "all"
AUTO_DOWNLOAD_MODES = [AUTO_DOWNLOAD_OFF, AUTO_DOWNLOAD_RINGS, AUTO_DOWNLOAD_ALL]

DEFAULT_POLL_INTERVAL = 20
DEFAULT_AUTO_DOWNLOAD = AUTO_DOWNLOAD_RINGS
DEFAULT_CONVERT_MP4 = True

# How far back each poll looks. Must comfortably exceed the poll interval so a
# clip indexed slightly late is still seen.
LOOKBACK_SECONDS = 900

EVENT_RING = "ring"
EVENT_MOTION = "motion"
EVENT_TYPES = [EVENT_RING, EVENT_MOTION]

# The hub reports a free-form video_type per clip. Anything matching these
# substrings counts as a doorbell press; everything else is motion.
RING_HINTS = ("ring", "doorbell", "call", "button", "visitor")

SIGNAL_NEW_CLIP = f"{DOMAIN}_new_clip"
