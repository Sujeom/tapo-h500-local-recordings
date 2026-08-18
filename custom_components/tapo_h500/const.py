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
SERVICE_SNOOZE = "snooze"
SERVICE_BACKUP_NAMES = "backup_names"
SERVICE_CLASSIFY_DOWNLOADS = "classify_downloads"
SERVICE_RESTORE_NAMES = "restore_names"

# Stamped into a backup so a future format change can be told from this one.
# Nothing reads it yet, and that is the point: by the time there is a second
# format, the first one's files are already out there unlabelled.
BACKUP_VERSION = 1

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

# ...and the same for recordings with a person in them.
#
# The two classes worth protecting separately, and deliberately only two. The
# hub names nine detection codes, and a retention number for each would be
# nine boxes on a form to express one idea: keep the clips that mattered. A
# press is somebody at the door and a person is somebody there at all; motion
# is a cat, vehicles are the road, and a face code never fires without the
# person code beside it.
CONF_KEEP_PERSON = "keep_person"
DEFAULT_KEEP_PERSON = 0
# Code 6. Named against three app-labelled events and confirmed by a doorbell
# press that carried it; see DETECTION_NAMES.
PERSON_CODES: set[int] = {6}

# Code 19, and the one detection that must not be allowed to scroll past.
#
# Confirmed on real hardware: the front camera was lifted off its mount at
# 11:16:16 on 2026-08-13 and logged alarm_type 19. Everything else here is
# something that happened outside the house; this is somebody interfering with
# the camera itself, and if it is real then the recordings after it are the
# ones that will be missing.
#
# It already has a binary sensor, which holds for thirty seconds and then
# clears -- correct for a graph and useless for a fact somebody needs to see
# whenever they next open Home Assistant. Hence a repair issue as well: it
# stays until the detection ages out of the poll window.
TAMPER_CODES: set[int] = {19}

# The daily digest is OFF unless asked for. A summary nobody requested is the
# kind of notification people mute the whole integration over.
CONF_DIGEST_TIME = "digest_time"
DEFAULT_DIGEST_TIME = ""

# Which detections are worth the disk, as alarm codes. Empty means all of them,
# which is what every existing installation has and what it keeps.
#
# off / rings / all was the only choice, and on this firmware two of those three
# are the same: a TD21 doorbell labels every clip video_type "2", so ring-only
# matched nothing and downloaded nothing until code 17 was identified. Even
# now, "rings" and "everything" is a poor pair of options for a camera facing a
# road -- the clips people actually go back for are the ones with a person in
# them, and vehicles are the traffic.
#
# Stored as the codes rather than as labels, because the labels are a reading of
# what the hub means and have already been corrected once.
CONF_DOWNLOAD_TYPES = "download_types"

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

# How often the media port's handshake is checked. The known failure takes
# hours to develop, so fifteen minutes hears about it long before anyone
# misses a photograph, at a cost of one unauthenticated TCP exchange.
MEDIA_CHECK_SECONDS = 900

# Opt-in self-healing: restart the hub when its media service is seen in
# either failure state. Off by default -- an automatic reboot is the owner's
# decision -- and never more often than the cooldown, which makes a reboot
# loop impossible however long the failure persists.
CONF_AUTO_RESTART = "auto_restart"
DEFAULT_AUTO_RESTART = False
AUTO_RESTART_COOLDOWN = 6 * 3600
EVENT_AUTO_RESTART = f"{DOMAIN}_auto_restart"

# How often the hub is asked to check the cloud for newer firmware. Hours:
# the check makes the HUB phone TP-Link, and the app shows the same restraint.
FIRMWARE_CHECK_SECONDS = 21600

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

# ...and per camera, because one pair of numbers cannot fit two cameras.
#
# The baseline is already the camera's own rate, which handles a busy door and
# a quiet gate seeing different amounts. What it cannot handle is the two
# meaning different things: on a doorbell facing a pavement, three times
# typical is a Saturday, and on a back gate it is somebody in the garden.
#
# Offered as three levels rather than as two numbers per camera. A multiplier
# and a floor are the right model for the code and the wrong question to ask a
# person -- "how many times its own hourly average, and what is the minimum
# count below which nothing counts" is not something anyone knows the answer
# to about their own front door.
#
# "normal" is the pair that has always been used, so an existing installation
# behaves exactly as it did.
CONF_SENSITIVITY = "sensitivity"
DEFAULT_SENSITIVITY = "normal"
SENSITIVITY_LEVELS: dict[str, tuple[float, int]] = {
    "sensitive": (2.0, 3),
    "normal": (UNUSUAL_MULTIPLIER, UNUSUAL_FLOOR),
    "relaxed": (5.0, 8),
}

# How far apart two recordings can be and still be one visit, and how long a
# visit by an unrecognised face has to last before it is worth saying so.
#
# The hub reports moments, not presence. Someone standing at the door for four
# minutes produces a string of short clips, and every other count here treats
# those as separate events -- which is why nothing yet notices the difference
# between reading a house number and waiting by the door. The busy-camera
# signal is a rate over an hour and the night signal is about the clock;
# neither sees this.
#
# Two minutes for the gap, because a clip is about fifteen seconds and a person
# who moves out of frame and back is one visitor. Three minutes for the
# threshold: a delivery is under a minute at the door, and a judgement rather
# than a measurement -- there is no data here on how long people loiter.
LOITER_GAP = 120
LOITER_SECONDS = 180

# The other end of the same measurement: a visit short enough to be a delivery.
#
# Under a minute at the door, in daylight, by somebody the hub did not
# recognise. A guess and named like one -- nothing the hub reports says
# "courier", a canvasser looks identical, and so does somebody checking whether
# the house is empty.
#
# The hold exists because this can only be known afterwards. While somebody is
# at the door they have been there for one clip, and so has everybody who is
# about to stay ten minutes; the length is final only once the visit has ended.
# Five minutes is long enough for an automation to notice.
DELIVERY_SECONDS = 60
DELIVERY_HOLD = 300

# How many hours of nothing at all before a camera is called silent.
#
# Silence is the only evidence available. The 16 fields in the paired-device
# record carry no online flag, no signal strength and no battery -- measured,
# and recorded in the protocol notes along with the eleven battery methods that
# all answer -40106. A camera that has fallen off the Wi-Fi, run flat or been
# unplugged looks exactly like a camera where nothing has happened, and Home
# Assistant shows both as a set of entities holding their last value.
#
# So this is a weak signal, and named accordingly: "silent", not "offline". It
# is still the difference between noticing in a day and noticing when you next
# need the footage.
#
# Capped at the poll window, because nothing longer is knowable: the hub is
# asked for a day of recordings, and "nothing in three days" cannot be
# distinguished from "nothing in one" without a database this does not have.
# The silent watchdog flags when this many events should have happened by
# now, on the camera's own hourly history. Three: one missing event is a
# quiet spell, two is a slow day, three predicted and none delivered is a
# camera that has stopped. Poisson says a rate-3 window is silent about 5%%
# of the time, which is the false-alarm price of hearing about a dead
# camera in hours instead of a day.
SILENT_EXPECTED = 3.0

# How many days the cards list when a card has not chosen for itself. One
# place instead of eight card editors; a card with its own `days:` still wins.
CONF_CARD_DAYS = "card_days"
DEFAULT_CARD_DAYS = 1

CONF_SILENT_HOURS = "silent_hours"
DEFAULT_SILENT_HOURS = 24

# Forecasting when the hub starts overwriting.
#
# There is no history to read: the hub reports how full it is now and nothing
# about how full it was, and this integration keeps no database. So the trend
# is sampled while Home Assistant runs, from the status refresh that already
# happens once a minute, and the forecast is unavailable until enough of it
# has accumulated -- an hour, because the figure is rounded to a tenth of a
# percent and two readings a minute apart measure that rounding.
#
# Held in memory only, and deliberately so. Writing a sample a minute to disk
# to survive a restart is a lot of machinery for a number that becomes useful
# again an hour after startup.
#
# 1440 samples is a day at one a minute, which is as much as the forecast can
# usefully weigh: beyond that the rate that mattered is last week's.
MIN_TREND_SECONDS = 3600
STORAGE_SAMPLES = 1440
# A fall bigger than this means the disk was emptied -- formatted, swapped, or
# loop recording finally catching up -- not that it drained. Fitting a line
# across that drop forecasts from a slope that never happened.
EMPTIED_PERCENT = 1.0

SIGNAL_NEW_CLIP = f"{DOMAIN}_new_clip"
SIGNAL_FACES_CHANGED = f"{DOMAIN}_faces_changed"

# Fired the first time a known person is seen on a given local day.
#
# The ordinary event fires on every detection, which is right for a doorbell
# and wrong for a person: someone who works from home crosses the front camera
# a dozen times a day and only one of those is "they got home". Nothing else
# here can tell the two apart, and they deserve different notifications --
# the twelfth is noise, the first is news.
#
# Only for faces that have been named. An unnamed id arriving is a stranger
# appearing, which is what the detection event already says.
EVENT_ARRIVAL = f"{DOMAIN}_arrival"

# How close together two CAMERAS' visits have to be to be one arrival.
#
# Two doorbells covering one path see the same person twice, and a visit event
# per camera is two notifications about one arrival -- the thing this whole
# event exists to stop, reappearing one level up.
#
# Thirty seconds covers cameras with overlapping views and somebody walking
# straight past. Beyond that, a shared face id is required as well: a person
# the hub recognised at the gate and again at the door within the direction
# window is evidently one journey, where two strangers two minutes apart at
# different cameras are evidently not. Only ever across cameras -- one camera's
# own recordings are already grouped into visits, and re-grouping them here
# would swallow a genuine second visitor.
#
# A judgement, like the other windows here. There is no data on how long people
# take to walk between two doorbells.
ENCOUNTER_SECONDS = 30

# Fired once when a visit begins, where the arrival event covers only people
# who have been named.
#
# The detection event is per recording, and the hub reports moments rather than
# presence: somebody standing at the door for four minutes arrives as a string
# of fifteen-second clips, so an automation wired to detections sends sixteen
# notifications about one visitor. Everything needed to fix that already
# existed -- sessions() has grouped clips into visits since the loitering
# sensor -- and nothing was announcing it.
#
# Fired at the START of a visit, which is the only time it can be useful for a
# notification, and therefore carries only what is known by then: the first
# recording. What the visit turned out to be is the delivery and loitering
# sensors' job, and both are necessarily retrospective.
EVENT_VISIT = f"{DOMAIN}_visit"

# How many recent sightings to keep on a face's trail.
#
# Following one person between cameras is real rather than inferred: measured
# on this hub, face ids are hub-wide, and two of six ids appeared on both
# doorbells. The trail is what that looks like as data. Capped because it is an
# entity attribute -- the whole list is written to the state machine on every
# update, and an uncapped one would grow with the poll window.
FACE_TRAIL_MAX = 20

# How recently someone must have been seen to count as "here".
#
# Weaker than it sounds and named accordingly: not being seen is not evidence
# of absence. A camera watches a doorstep, not a house, so someone indoors is
# invisible to it. This answers "was seen just now", which is true or false,
# rather than "is home", which this hardware cannot know.
FACE_PRESENCE_WINDOW = 600

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

# How long a circuit of the house can take and still be one circuit.
#
# Longer than DIRECTION_WINDOW, which covers one hop between adjacent cameras.
# Going round a house and coming back to where you started is a walk, not a
# step, and somebody who passes the front door twice in ten minutes has done
# something different from somebody who passes it twice in an afternoon.
#
# A judgement, like the direction window: there is no data here on how long
# anybody takes to walk round a house.
PROWL_WINDOW = 600

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
# Only the poll interval: it is captured when the coordinator is built, so a
# change genuinely needs a rebuild. Every other option -- the download mode,
# MP4 conversion, all three keep counts, the download types -- is read from
# entry.options at the moment it is used, so reloading for one of those bought
# a fresh login (the one thing that wedges this hub) for a value the running
# coordinator would have picked up anyway.
RELOAD_ON_CHANGE = (
    CONF_POLL_INTERVAL,
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
