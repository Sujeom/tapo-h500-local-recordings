"""Constants for Tapo H500."""

DOMAIN = "tapo_h500"
CONF_CLOUD_PASSWORD = "cloud_password"
DATA_HUBS = "hubs"
DATA_CARD = "card_registered"
DATA_PREVIEW = "preview_view_registered"

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
CONF_KEEP_DOWNLOADS = "keep_downloads"

AUTO_DOWNLOAD_OFF = "off"
AUTO_DOWNLOAD_RINGS = "rings"
AUTO_DOWNLOAD_ALL = "all"
AUTO_DOWNLOAD_MODES = [AUTO_DOWNLOAD_OFF, AUTO_DOWNLOAD_RINGS, AUTO_DOWNLOAD_ALL]

DEFAULT_POLL_INTERVAL = 20
# An H500 with TD21 doorbells labels every clip video_type "2", so ring-only
# filtering matches nothing and downloads nothing. Defaulting to rings made the
# feature a silent no-op; default to all until the ring code is identified.
DEFAULT_AUTO_DOWNLOAD = AUTO_DOWNLOAD_ALL
DEFAULT_CONVERT_MP4 = True
# 0 keeps everything. Any positive number is how many of the newest
# automatically downloaded clips to keep per camera.
DEFAULT_KEEP_DOWNLOADS = 0

# How far back each poll looks. A day's window costs the same single call as a
# short one and is what makes "last activity" and the 24h counts meaningful;
# without it those sensors would be blank whenever nothing happened recently.
LOOKBACK_SECONDS = 86400

# The hub rejects a siren volume of 0 or 11 with -40209, so the usable range is
# 1-10 and Home Assistant's 0.0-1.0 level is scaled onto it.
SIREN_VOLUME_MIN = 1
SIREN_VOLUME_MAX = 10

EVENT_RING = "ring"
EVENT_MOTION = "motion"
EVENT_TYPES = [EVENT_RING, EVENT_MOTION]

# The hub reports a free-form video_type per clip. Anything matching these
# substrings counts as a doorbell press; everything else is motion.
RING_HINTS = ("ring", "doorbell", "call", "button", "visitor")

SIGNAL_NEW_CLIP = f"{DOMAIN}_new_clip"

# Video is remuxed, not re-encoded. Audio is re-encoded because the hub's TS
# audio codec is not always one MP4 can carry.
#
# "-f mp4" is load bearing: the clip is written to a temporary ".mp4.part"
# first, and ffmpeg picks its muxer from the extension. Without an explicit
# format it fails with "Unable to choose an output format" and every download
# dies at the conversion step.
CONVERT_ARGS = ["-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart",
                "-f", "mp4"]

# A preview of a clip that is still only on the hub. The hub streams a bounded
# window, so one decodable frame does not need the whole recording: measured on
# firmware 1.3.20, two seconds yields ~230 KB in ~2s and decodes cleanly, where
# a full 15-second clip is ~3.4 MB. Bounded twice — a short window at the hub
# and a byte cap here — because the window alone is the hub's estimate, not a
# promise.
PREVIEW_SECONDS = 2
PREVIEW_MAX_BYTES = 262_144

# One frame, scaled down. A full 2304x1296 frame is ~530 KB, which is absurd
# for something the card renders at 96x54; 640 wide is ~65 KB and still sharp
# on a high-DPI screen. Height -2 keeps the aspect ratio even.
THUMBNAIL_ARGS = ["-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4"]
