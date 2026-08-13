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
# substrings counts as a doorbell press; everything else is motion. Kept as a
# fallback: on firmware 1.3.20 video_type is "2" for every clip, so it never
# matches and the detection log below is what actually classifies.
RING_HINTS = ("ring", "doorbell", "call", "button", "visitor")

# alarm_type codes from searchDetectionList on firmware 1.3.20. Six of the nine
# are now named, each against something observed rather than guessed.
#
# The Tapo app labelled three side-doorbell events, and they differ by exactly
# one code each -- which names two of them outright:
#
#   "motion + person"          -> [2, 6,    22]
#   "person + motion + car"    -> [2, 6, 8, 22]   the car adds 8
#   "person + dog + motion"    -> [2, 6, 9, 22]   the dog adds 9
#
# That leaves {2, 6, 22} for "motion + person", one code more than labels. Two
# things separate them. Code 20 (a recognised face) co-occurs with 6 in all 5
# of its detections but with 22 only once, and a face is a person. And the
# confirmed doorbell press carried 6 but not 22, and someone pressed it. So 6
# is person, and 2 is motion -- it is set on 31 of 35 detections, the base
# signal nearly everything carries.
#
#   17  doorbell. The front doorbell was rung at 14:42:25 on 2026-08-13 and it
#       was the only event on that camera in six hours.
#   20  face. All 5 of its detections carried a face_id; no other code ever did.
#
# Still unnamed, on purpose: 22 occurs 18 times and always alongside 6, so it
# is some subset of person events that nothing observed distinguishes; 10 has
# only ever appeared beside 17, so it is part of the doorbell signal; 19 is
# rare and unattributed. They display as their number, because a confident
# wrong label on a recording is worse than an honest "type 22".
DETECTION_NAMES = {
    2: "motion",
    6: "person",
    8: "vehicle",
    9: "pet",
    17: "doorbell",
    20: "face",
}

# Which codes mean a doorbell press. 17 is confirmed against a real press; 10
# rides along with it every time but has never been seen alone, so adding it
# would claim more than was observed.
RING_ALARM_TYPES: set[int] = {17}

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
