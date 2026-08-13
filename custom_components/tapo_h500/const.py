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

# How often to poll. This is the entire notification latency budget: nothing
# the hub sees can reach Home Assistant sooner than the next poll.
#
# Measured against the hub on firmware 1.3.20, held session, median of five:
#
#     detections()  per camera      19ms
#     recent()      per camera      17ms
#     cameras()                     58ms
#     hub_status()  14 batched     430ms
#
# So the work that actually matters -- the detection log for two cameras -- is
# about 40ms. The old 20s interval was not a hub limit at all; the device
# answers roughly five hundred times faster than it was being asked. At 2s a
# poll uses a few percent of the time available, and an event is announced
# within a second of the hub seeing it.
#
# Safe because every call reuses the one authenticated session opened by
# connect(). A poll costs ordinary requests, not logins, and it is repeated
# logins -- not request volume -- that wedges an H500.
DEFAULT_POLL_INTERVAL = 2

# Two things do not belong on that hot path. Hub status is LED state, siren
# config, storage and firmware, and at 430ms it is by far the most expensive
# call the integration makes. The camera list only changes when a camera is
# paired or removed. Both are refreshed on an age in seconds rather than a
# poll count, so they stay correct if the interval is changed in options.
STATUS_MAX_AGE = 60
CAMERAS_MAX_AGE = 300
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

# alarm_type codes from searchDetectionList on firmware 1.3.20. All nine are
# named; each entry records what it was named against.
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
#   17  doorbell. The front doorbell was rung at 10:42:25 on 2026-08-13 and it
#       was the only event on that camera in six hours.
#   19  theft. The front camera was lifted off its mount at 11:16:16 the same
#       morning: alarm_type 19, alongside person and face because someone was
#       standing there doing it. It is also one of only two codes ever seen
#       alone, which fits an alarm that can fire with nobody recognised.
#   20  a face the hub matched to an individual. Every one of its detections
#       carried a face_id and no other code ever did. "Matched", not "named":
#       the 7:46pm event on 2026-08-12 already carried 20 and a face_id long
#       before that person was given a name in the app, and naming them did not
#       change the record by a single byte. The hub clusters faces and hands out
#       ids; the name lives app-side.
#
#   22  a face the hub could NOT match to an individual. Inferred rather than an
#       app label, but the split is clean: code 20 carries a face_id in 6 of 6
#       detections, and 22 in 1 of 18 -- and that single exception is the only
#       event where both appear, so the id there belongs to the 20. Excluding
#       it, 22 is 0 for 17. It also cannot be body-or-person detection: that
#       would fire on all 29 person events, and 22 fires on 18. So 20 and 22
#       partition faces into recognised and not. An app label saying
#       "unknown"/"stranger" on a 22 event would settle it outright.
#
#   10  a doorbell press nobody answered. Named at the owner's identification,
#       and it fits the mechanism: a Tapo doorbell places a call, a call has an
#       outcome, and that explains a code which cannot exist without 17.
#
#       Held to a lower standard of proof than the others, and deliberately
#       recorded as such. Measured over 42 detections across 7 days, 10 and 17
#       are perfectly coupled -- 5 events carry 17, all 5 carry 10, and neither
#       has ever been seen without the other:
#
#         Wed Aug 12  9:16:23 PM  Side    [2, 10, 17]
#         Thu Aug 13 10:42:25 AM  Front   [6, 10, 17]        the confirmed press
#         Thu Aug 13 11:52:57 AM  Front   [6, 10, 17, 20]
#         Thu Aug 13 11:53:33 AM  Front   [2, 6, 10, 17, 22] 36s after the last
#         Thu Aug 13 12:12:22 PM  Side    [10, 17]
#
#       Two of those presses are 36 seconds apart, which is what ringing again
#       after no answer looks like, and none of the five was answered in the
#       app. That is the weakness: "10 means missed" and "10 is simply part of
#       every press" both predict 5 of 5, so this measurement cannot tell them
#       apart. Only an ANSWERED press can. If one ever logs 17 without 10 the
#       name is confirmed; if an answered press still carries 10 it is wrong
#       and should be removed rather than reworded.
#
#       Because 10 accompanies every 17, describe_detection() collapses the
#       pair into "doorbell (missed)" rather than listing both and announcing
#       the same press twice.
DETECTION_NAMES = {
    2: "motion",
    6: "person",
    8: "vehicle",
    9: "pet",
    10: "missed doorbell",
    17: "doorbell",
    19: "theft",
    20: "face",
    22: "unknown face",
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
