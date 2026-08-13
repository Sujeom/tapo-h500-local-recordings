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
SERVICE_NAME_FACE = "name_face"
SERVICE_DESCRIBE_RECORDING = "describe_recording"
SERVICE_DAILY_SUMMARY = "daily_summary"
SERVICE_FIND_FACE = "find_face"
SERVICE_EXPORT_RECORDING = "export_recording"

# What to ask a vision model about a still from a doorbell clip.
#
# Deliberately narrow. A model given an open-ended prompt will speculate about
# intent -- "a suspicious individual loitering" -- from one frame of someone
# reading a house number, and that speculation would then be read back as if
# the hub had reported it. The hub's own detection codes already say what was
# there; this only adds the detail they cannot carry.
DESCRIBE_PROMPT = (
    "This is a single still frame from a doorbell camera recording. "
    "Describe only what is visibly present in one or two plain sentences: "
    "people and what they appear to be carrying or doing, vehicles, animals, "
    "packages, and the weather or time of day if obvious. "
    "Do not speculate about intent, identity, or whether anything is "
    "suspicious. If the frame is too dark or blurred to tell, say so."
)

# Face names live on the config entry, not on each card.
#
# The hub clusters faces and hands out stable ids but will not say who anyone
# is -- there is no face library to look an id up in, and no request that
# returns one. The name has to come from the owner. It used to be typed into
# every card that showed faces, so three cards meant three copies to keep in
# step; here one map serves every card, the per-face sensors and anything else
# that wants it. A card may still override it locally.
CONF_FACE_NAMES = "face_names"

# Subdirectory of Home Assistant's "local" media directory that holds clips.
MEDIA_DIR = "tapo_h500"
CARD_URL = "/tapo_h500_static/tapo-h500-card.js"

CONF_POLL_INTERVAL = "poll_interval"
CONF_AUTO_DOWNLOAD = "auto_download"
CONF_CONVERT_MP4 = "convert_mp4"
CONF_KEEP_DOWNLOADS = "keep_downloads"
# Doorbell presses are worth keeping longer than motion. One number for
# everything meant a busy afternoon of motion could evict the press that
# actually mattered. 0 means "same as everything else".
CONF_KEEP_RINGS = "keep_rings"
DEFAULT_KEEP_RINGS = 0

# The daily digest is OFF unless asked for. A summary nobody requested is the
# kind of notification people mute the whole integration over.
CONF_DIGEST_TIME = "digest_time"
DEFAULT_DIGEST_TIME = ""

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
# How far the poll interval backs off while the hub is not answering.
#
# At 2s a failing hub is asked thirty times a minute, and pytapo
# re-authenticates when its token stops working -- so a hub that has stopped
# responding gets a stream of fresh logins, which is precisely what wedges an
# H500 and precisely when it can least afford it. The interval doubles per
# consecutive failure up to this cap and snaps back on the first success.
POLL_BACKOFF_MAX = 300

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

# How long a detection binary sensor stays on after the hub reports it.
#
# A detection is a moment, not a state: the hub says "a person, at 17:41:09"
# and never says they left. The event entity models that correctly and is the
# right thing to trigger on. These sensors exist for the things an event
# cannot do -- appear on a history graph, answer "has anything moved in the
# last hour", or gate another automation with a condition -- and all of those
# need the moment held open for a little while.
#
# 30s comfortably outlasts a ~15s clip, so consecutive detections in one
# visit read as one continuous presence rather than a stutter.
DETECTION_HOLD = 30

# What counts as an unusual hour, measured against the camera's own recent
# rate rather than a fixed number: a doorbell on a main road and a back gate
# do not agree on what "busy" means.
#
# The floor exists because a ratio breaks at both ends. A quiet camera has a
# baseline near zero, where a single delivery is infinitely above typical;
# below four events in an hour nothing is flagged at all.
UNUSUAL_MULTIPLIER = 3.0
UNUSUAL_FLOOR = 4

SIGNAL_NEW_CLIP = f"{DOMAIN}_new_clip"
SIGNAL_FACES_CHANGED = f"{DOMAIN}_faces_changed"

# How many recent sightings to keep on a face's trail.
#
# Following one person between cameras is real rather than inferred: measured
# on this hub, face ids are hub-wide, and two of six ids appeared on both
# doorbells. The trail is what that looks like as data. Capped because it is an
# entity attribute -- the whole list is written to the state machine on every
# update, and an uncapped one would grow with the poll window.
FACE_TRAIL_MAX = 20

# How many times an unnamed face must be seen before the integration suggests
# naming them.
#
# The hub invents an id for every face it clusters, most of which belong to
# people who pass once and never return. Someone seen repeatedly is different:
# a neighbour, a regular delivery, a member of the household the hub has not
# been told about. That is worth one prompt, and the count is what separates
# the two without guessing.
NAME_PROMPT_SIGHTINGS = 5

# Where each camera sits between the street and the door, so a trail can be
# read as a direction.
#
# Stored per camera as a rank: lower is nearer the street, higher is nearer
# the door. A gate camera might be 0 and a doorbell 1. The integration cannot
# work this out for itself -- the hub reports no geometry, and camera order in
# the paired list is the order they were added, which means nothing.
#
# Unset means unknown, and an unknown rank produces no direction at all
# rather than a guessed one.
CONF_CAMERA_ORDER = "camera_order"

# How long two sightings can be apart and still count as one journey.
#
# Someone walking from the gate to the door takes a few seconds. Two sightings
# ten minutes apart are two visits, and calling that "approaching" would be
# an invention. Measured against nothing in particular -- there is no data on
# walking speed here -- so this is a judgement, and a generous one.
DIRECTION_WINDOW = 180

# The hours an unfamiliar face counts as notable, as [start, end) local.
#
# An unknown face at three in the afternoon is a delivery; the same face at
# three in the morning is not. Nothing else here distinguishes them, and the
# difference is the whole point of a separate signal -- it is what earns a
# different notification sound.
#
# 22:00 to 06:00 by default, wrapping midnight. Configurable because a night
# shift makes a nonsense of anyone else's idea of night.
CONF_NIGHT_START = "night_start"
CONF_NIGHT_END = "night_end"
DEFAULT_NIGHT_START = 22
DEFAULT_NIGHT_END = 6

# Options that actually change how the integration talks to the hub. A change
# to one of these needs a reload; a change to anything else does not.
#
# Naming a face used to reload the entry like any other option change, which
# tore down the coordinator mid-request -- the card that asked for the name
# then failed with "cannot get data from the hub" -- and, worse, opened a
# fresh login. Repeated logins are the one thing that wedges an H500, so
# recording a name must not cost one.
RELOAD_ON_CHANGE = (
    CONF_POLL_INTERVAL, CONF_AUTO_DOWNLOAD, CONF_CONVERT_MP4,
    CONF_KEEP_DOWNLOADS, CONF_KEEP_RINGS,
)

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
