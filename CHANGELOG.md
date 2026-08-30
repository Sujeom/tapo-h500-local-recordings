# Changelog

Generated from the tag annotations by `tools/publish-releases.py --changelog`. Every entry is the note written when that version was tagged.

## v0.123.0 &mdash; 2026-08-19

Two worked examples

Casting the doorbell clip to a display seconds after the press, and a
weekly backup of every typed-in setting -- both as commented YAML in
examples/.

## v0.122.0 &mdash; 2026-08-19

The device page leads with what people check

Five analyst sensors move to the diagnostic section -- still recording
statistics exactly as before, just shelved. Activity, health and
storage level lead.

## v0.121.0 &mdash; 2026-08-19

Snooze by voice

"Snooze the doorbell" in Assist quiets notifications for an hour on
every hub. Recording continues; the switch cancels early.

## v0.120.0 &mdash; 2026-08-19

A Today folder in the media browser

All cameras merged, newest first, one click from the root -- what
happened today without picking a camera first.

## v0.119.0 &mdash; 2026-08-19

Quiet hours in the notification blueprint

Two time inputs silence ordinary notifications between them, midnight
wrap included; notable events -- tampering, an unfamiliar face at
night -- punch through.

## v0.118.0 &mdash; 2026-08-19

A live pulse while something is happening

Cards show a pulsing Recording… dot for the first 45 seconds after a
detection -- the window between the hub noticing and the clip
existing, and the closest thing to live view this hardware offers.

## v0.117.0 &mdash; 2026-08-19

Page through days on the recordings card

Back and forward arrows in the card header browse one local day at a
time, with the date as a jump home to today.

## v0.116.0 &mdash; 2026-08-19

Named people carry their photograph

Per-person sensors get an entity_picture: the frame of their newest
sighting, generated from the hub if never downloaded, cached per
sighting so the frontend is not refetching per poll.

## v0.115.0 &mdash; 2026-08-18

The camera picture heals with the hub

Frame-fetch attempts burned during a media outage are cleared on
recovery, so the first look afterwards re-fetches instead of showing a
stale frame until the next event.

## v0.114.0 &mdash; 2026-08-18

The cure is verified, not assumed

Minutes after an automatic restart the deep media check is forced due:
bytes prove the cure and re-arm everything, an empty answer trips the
breaker while the failure is still inside its cure window.

## v0.113.0 &mdash; 2026-08-18

A restart that does not cure stops being tried

If the media failure returns within half an hour of an automatic
restart, the breaker trips: automatic restarts pause, a repair notice
says this is something new, and only recordings actually serving again
re-arms the automation.

## v0.112.0 &mdash; 2026-08-18

The hollow-session failure is found within the hour

A quiet day no longer hides it: with no media evidence for an hour and
an indexed clip available, the coordinator fetches two bounded seconds
itself and feeds the same counters the downloads feed. Bytes are the
all-clear, so recovery is noticed without a download too.

## v0.111.0 &mdash; 2026-08-18

Self-healing, opted into

An off-by-default option restarts the hub automatically when its
recording service fails -- the one failure a reboot provably cures --
at most once per six hours, loudly. A fourth blueprint sends
everything that needs a human to the phone.

## v0.110.0 &mdash; 2026-08-18

The hollow-session failure is now seen

Two consecutive downloads that finish cleanly with zero video flag the
hub's second media failure mode -- measured on hardware today -- on
the same repair notice and sensor as the wedge, both naming the
Restart button as the cure.

## v0.109.0 &mdash; 2026-08-18

Local only, by design -- and now enforced

The firmware entity reads the hub's cached answer instead of
commanding a cloud check; a test pins that nothing in the integration
ever tells the hub to contact TP-Link. The README states the mission
and the two WAN-blocking notes: keep NTP, turn auto-upgrade off.

## v0.108.0 &mdash; 2026-08-18

Restart the hub from Home Assistant

button.<hub>_restart -- pytapo's unambiguous rebootDevice verb, one
press on the device page, about two minutes of downtime, recordings
intact. Cameras stay unrestartable: the hub offers no per-camera
addressing (measured).

## v0.107.0 &mdash; 2026-08-17

The case-D experiment runs itself

When the sentinel sees the wedge, the next media session tries a fresh
player_id -- once per episode -- and the session log records whether
that alone recovers service. Field evidence for the wedge
investigation, gathered at the only moment it can be.

## v0.106.0 &mdash; 2026-08-17

The device page says when hub firmware is behind

An update entity fed by the cloud check the hub itself performs, a few
times a day. Report-only; the app does the upgrading.

## v0.105.0 &mdash; 2026-08-17

Name a face from the repair notice

The unnamed-face notice is fixable: press Fix, type the name, done.
Two taps where there was a navigation.

## v0.104.0 &mdash; 2026-08-17

Face search over the whole archive

Sidecars carry face ids now, and find_face merges the downloaded
history into its answer -- months deep instead of the hub's one day.

## v0.103.0 &mdash; 2026-08-17

The type folders cover the whole archive

tapo_h500.classify_downloads backfills the missing sidecars from the
hub's own detection log, one query per unclassified camera-day, safe
to re-run.

## v0.102.0 &mdash; 2026-08-17

Backups carry every setting somebody chose

backup_names now includes sensitivity, the night window, download
types, keep counts, silent hours and the card days; restore puts them
back, filtered to the known keys. Old backups restore unchanged.

## v0.101.0 &mdash; 2026-08-17

The wedge becomes automatable

binary_sensor.<hub>_recording_service_problem turns on when the media
sentinel sees the known wedge, so an automation can notify or
power-cycle. Diagnostics downloads now carry the sentinel verdict,
session count and download-failure counts.

## v0.100.0 &mdash; 2026-08-17

Tampering sounds the alarm at any hour

The high-importance notification channel now covers camera tampering
around the clock, alongside the unfamiliar-face-at-night rule.

## v0.99.0 &mdash; 2026-08-17

Snooze from the notification

A Snooze 1h button on the alert itself: notifications pause for an
hour, recording never stops, nothing needs turning back on. The photo
notification swaps it for Save clip, keeping every alert within
Android's three-button budget.

## v0.98.0 &mdash; 2026-08-17

Filter chips on the recordings card

All / Presses / People / Pets / Vehicles across the top of the
recordings card, filtering the listed clips client-side by their
detection codes.

## v0.97.0 &mdash; 2026-08-17

The docs catch up with the last fourteen releases

README and reference now describe the adaptive watchdog, the media
sentinel and its terminal tool, the Save clip button, the optional
end_time, the sidecars and type folders, and the global days setting.

## v0.96.0 &mdash; 2026-08-17

A publisher for the release notes already written

tools/publish-releases.py turns every annotated tag into a GitHub
Release, title from the annotation's first line, body from the rest.
Idempotent; needs only a GITHUB_TOKEN with contents: write.

## v0.95.0 &mdash; 2026-08-17

Quick replies ruled out, batching evidence recorded

Seven chime/quick-reply getters all answer -40106 on this firmware;
documented beside the other advertised-but-unreachable surfaces, with
the multipleRequest batching evidence recorded in the protocol notes.

## v0.94.0 &mdash; 2026-08-17

Half the requests per poll

Each camera's clip and detection searches now ride one
multipleRequest, proven on hardware with byte-identical results. A hub
that refuses the envelope degrades to the old single calls and
remembers.

## v0.93.0 &mdash; 2026-08-17

A terminal one-liner for the media-wedge question

tools/check-media.py <host>: healthy, wedged, unreachable or silent,
from one unauthenticated TCP exchange. Stdlib only, no login.

## v0.92.0 &mdash; 2026-08-17

One days-to-show setting for every card

Configure gains "Days the cards show by default". Cards without their
own days follow it; a card with days set, or a summary-family card,
keeps its own. Read at call time -- changing it costs no login.

## v0.91.0 &mdash; 2026-08-17

Browse the archive by what is in it

Media -> Tapo H500 gains Doorbell presses, People, Vehicles and Pets
folders spanning every camera and day on disk, fed by a small JSON
sidecar each download now writes. Clips downloaded before this update
appear only under their camera and date.

## v0.90.0 &mdash; 2026-08-17

Save a clip from the notification

The photo notification carries a Save clip button: one press downloads
that exact recording, and manual downloads are never pruned. The
download service now accepts a start time alone, looking the end up in
the hub's own index.

## v0.89.0 &mdash; 2026-08-17

Repeated download failures become a repair notice

Three consecutive failed automatic downloads on one camera raise a
notice naming the camera, instead of warnings in a log nobody reads.
Any success resets it; the notice clears itself.

## v0.88.0 &mdash; 2026-08-17

The wedge is noticed before the photos go missing

Every fifteen minutes the media port's unauthenticated handshake is
checked -- one TCP exchange, no login. When the hub answers with the
known zero-byte close, a repair notice says recordings and photographs
will fail and that a hub reboot clears it.

## v0.87.0 &mdash; 2026-08-17

The silent-camera watchdog learns each camera's own rhythm

Flags when the camera's own hourly history says three events should
have happened by now -- a busy doorbell reads as dead within hours
while the quiet back gate stays patient, and a normal night accrues
nothing. The configured hours remain as a hard ceiling; the adaptive
half only ever flags earlier.

## v0.86.0 &mdash; 2026-08-17

The latest-event picture refreshes when the frame lands

image.<camera>_latest_event stamps a second time when the download
writes the file, so a dashboard that fetched too early is told to look
again.

## v0.85.0 &mdash; 2026-08-17

Region-suffixed hubs can set up

The model guard matches on the H500 prefix, so "H500(EU)" and
"H500 V2" install instead of failing setup forever.

## v0.84.0 &mdash; 2026-08-17

The first doorbell press after a restart notifies

The availability guard required a valid from_state, and an event entity
is unknown after every reload -- so the first real press afterwards was
silently swallowed. A fresh timestamp out of unknown now fires; a stale
one (a restored event replaying on startup) still does not.

## v0.83.0 &mdash; 2026-08-17

Options changes no longer cost a login

Only the poll interval reloads the entry now. The download mode, MP4
conversion and keep counts are read live, so changing them takes effect
immediately without re-authenticating to a hub that wedges under
repeated logins.

## v0.82.0 &mdash; 2026-08-17

The Camera button shows the right event

The camera and latest-event entities now fetch the newest indexed
clip's frame from the hub when no download has written it, so the
notification's Camera button shows the event it announced instead of
the previous one -- including for clips auto-download skips entirely.
One bounded fetch per clip, never retried per look.

## v0.81.0 &mdash; 2026-08-17

Media session lifecycle

- previews read the bounded window to the hub's own finished notification
  instead of dropping out at the byte cap
- a failed first refresh closes the login it just made, so a setup retry
  storm no longer becomes a login storm
- one debug record per media session: count, type, bytes, finished, outcome

## v0.80.0 &mdash; 2026-08-14

feat: read the hub's own reboot schedule

getReboot answers {"enabled":"off","day":"0","time":"03:00:00"} and was never
asked. It matters more than a settings dump usually does: a hub that reboots
itself has a gap in its recordings at that hour, and a gap in recordings is
indistinguishable from a camera that stopped working -- which is exactly what
the silent-camera watchdog would call it.

Unknown rather than "off" when the hub does not answer. These params come
from pytapo rather than from a live probe on this hardware, so an unanswered
call has to read as unknown; claiming "off" would be inventing a
reassurance. It rides the batched status request, so a wrong param shape
costs this reading and nothing else.

The time is reported only while the schedule is on -- it is stored either
way, and showing "03:00:00" for a hub that is not going to reboot is the
more alarming of the two wrong answers. The day is passed through
untranslated: the only value ever seen was 0, on a schedule that was
switched off, which says nothing about which day 0 means.

Read only. setReboot is still not called from anywhere: its params are
ambiguous between scheduling a reboot and performing one, and a wrong guess
reboots the hub mid-download.

## v0.79.0 &mdash; 2026-08-14

feat: show when the hub installs firmware, not only whether

switch.<hub>_automatic_firmware_updates said whether it happens. The hour it
happens at came back in the same block, was flattened into hub_readings on
every poll and reached nothing -- so the visible half of the setting was the
half that decides least. A hub that reboots itself to install firmware at
three in the afternoon is worth knowing about before it does.

Read from the block the switch already has to send back whole on every
toggle, so the two cannot disagree about the schedule, and it costs no extra
call: getFirmwareAutoUpgradeConfig is in the batched status request already.

The time is kept when updates are off -- the hub stores it either way, and a
blank there would read as a hub with no schedule rather than one with
updates turned off -- so `enabled` rides along as an attribute, with the
window the hub spreads updates over.

Drops a test asserting exactly one description used the attributes hook. A
second legitimate user makes the number wrong without making the intent
wrong, and what it was really protecting is covered by the None default.

## v0.78.0 &mdash; 2026-08-14

feat: publish the custom sound names the hub has always sent

used_audio_slots has read the hub's five slots since the sensor was added,
hub_readings has carried the names on every poll, and the reference
documented them as an attribute of sensor.<hub>_custom_sounds. That attribute
did not exist -- HubSensor had no attributes hook at all -- so the
documentation described a field nothing produced and the names were computed
once a minute and dropped.

"3" is a poor answer to "which sounds does the hub hold". The names are the
content of that reading and the count is a summary of them.

One optional callable on the description rather than a class for one lambda,
and None where a reading has nothing to add: an empty dictionary is a set of
attributes, and Home Assistant would record one on every state change of
every hub sensor.

## v0.77.0 &mdash; 2026-08-14

feat: firmware and hardware on the device page, and a model guard that runs

pytapo asks getDeviceInfo during login to work out what it is talking to, so
the model, firmware and hardware revision were already in hand before the
first poll. One model check read them and everything else was discarded --
and Home Assistant's device page sat there with an empty Firmware field on
an integration whose whole subject is one undocumented firmware.

The check itself never ran. The record is nested two levels down as
{"device_info": {"basic_info": {...}}}, and it was reading device_model off
the outer dictionary: the lookup missed, the default fired, the model came
back empty, and `if model and model != "H500"` was skipped every time. This
would have attached happily to a C200.

basic_info() unwraps it, and accepts the flat shape too because pytapo's
KLAP branch returns one and its own code tests for both. Junk gives an empty
record rather than raising -- this runs during setup, where a raise is a
config entry that will not load.

The versions also go into the diagnostics file, which is the first thing
anyone reading a bug report about an undocumented protocol wants. Still an
allow-list: model, sw_version and hw_version only, so the MAC, the device id
and the owner's own name for the hub stay out.

No extra round trip. The answer cannot change while the integration is
loaded, so it is read off the client rather than added to the status batch.

## v0.76.2 &mdash; 2026-08-14

fix: six of the sixteen diagnostics readings were always null

The allow-list named keys hub_readings does not produce. `storage_total`
against `storage_total_gb`, `storage_used` against `storage_used_percent`,
`led_enabled` against `led_on`, `face_detection_enabled` against
`face_detection`, `used_audio_slots` against `custom_sounds` -- near misses
every one, so all three storage figures, the LED state, face detection and
the audio slots came out null in every diagnostics download ever taken.

Nothing failed and nothing warned. The file was still valid, still redacted,
still the right shape, and simply said nothing about the readings its own
comment calls "what most reports need". That is how an allow-list fails, and
why the test now asserts every listed key against what hub_readings actually
returns rather than trusting the list to be right.

Found while auditing which hub readings reach an entity at all.

## v0.76.1 &mdash; 2026-08-14

fix: drop the "add names: to this card" hint from the faces cards

It described the only way naming used to work. Both cards now carry their
own naming route -- a "Name this face" button per tile on the faces card,
writing to the shared map on the config entry -- so the hint was pointing at
the worse of two options on a card where the better one was already on
screen, and telling people to hand-edit YAML per card is what the shared map
was added to stop.

The unused .hint rule goes with it from both cards.

## v0.76.0 &mdash; 2026-08-14

feat: a blueprint that announces the visitor, not the recordings

The two existing blueprints trigger on detections, which is right for a
doorbell press and wrong for a person. The hub reports moments rather than
presence, so four minutes at the door is sixteen fifteen-second clips, and
both blueprints carry machinery to cope with that -- repeat suppression in
one, mode: single in the other. The visit event removed the need for it and
nothing was using it.

This one triggers on tapo_h500_visit: once per visitor, and once between two
doorbells watching the same path. Optional strangers-only, which is the
useful setting on a busy door because the household crossing the front
camera all day is not news, and an after-dark gate.

The visit payload gains `night`, decided by the integration from the
configured window. A window that wraps midnight is the obvious thing to get
wrong -- 23 is inside 22-to-6 and 12 is not -- and every consumer would get
it wrong separately.

The blueprint's conditions are now rendered with real Jinja in the tests
rather than grepped for. A condition that is only searched for as text can
be inverted without any test noticing, and three of these are the difference
between "silent when snoozed" and "silent always".

## v0.75.0 &mdash; 2026-08-14

feat: say what was different about the day, not just what was in it

The digest counts. That is the honest thing to report and not what anybody
opens one for: "Front: 48 recordings (12 person, 3 vehicle)" is the same
sentence every day, so a day worth knowing about looks exactly like a day
that was not.

highlights() reports only what a day can have that most days do not, and
returns nothing at all when there was none -- an empty list is the common
case and is the entire design. A line that appears every day is not a
highlight.

Five things: a camera reporting tampering, which goes first however far down
the list its name would put it; a genuine peak hour; unfamiliar faces after
dark; somebody who stayed longer than three minutes; and a camera that
recorded nothing.

The peak is measured against the camera's own flat-day average rather than a
fixed count, for the same reason the unusual sensor is: a doorbell on a
pavement seeing five an hour all day has no peak, and a back gate seeing
five in one hour does. A fixed floor alone would print "busiest around 3pm"
every single day.

Everything comes from the same 24-hour window as the rest of this. There is
no comparison against last week, because there is no last week here.

Assist's "what happened today" leads with these and then reads the counts,
so a dull day sounds dull.

## v0.74.0 &mdash; 2026-08-14

feat: everyone at once, instead of five sensors compared by eye

One entity per named person is the right shape for automating and the wrong
one for looking at. With five people named, "is anybody about" meant reading
five sensors, five timestamps, and doing the arithmetic yourself.

sensor.<hub>_people_seen_recently is the same information in one place:
seen_recently, seen_today, not_seen, and everyone who has been named at all
-- so an empty house and an installation where nobody has been named yet are
different readings rather than both being zero.

seen_today is a local calendar day rather than the last 24 hours, which is
the difference between "not here this minute" and "has not been home all
day". The window reaches back a full day, so at one in the morning it still
holds last night, and a rolling day would report that as today.

Named for what it knows. A camera watches a doorstep, not a house: somebody
indoors is invisible to it, and so is somebody who left through a door with
no camera on it. not_seen is a list of people who have not been seen and is
deliberately not a list of people who are out.

Read from the merged people rather than the clusters, so either of somebody's
face ids seeing them counts as seeing them.

## v0.73.0 &mdash; 2026-08-14

feat: every worked-out signal is pickable from the automation editor

Nine detection codes were offered as device triggers and nothing else was.
Loitering, a likely delivery, a circuit of the house, an arrival and a visit
beginning -- the five things here that a camera cannot report and this
integration works out -- all needed hand-written YAML, which is the exact
gap the detection triggers were added to close.

Three kinds, because they arrive three ways. Detections stay on the camera's
event entity. The worked-out states attach to the binary sensors that are
already correct, matched on the unique id rather than the entity id, which
the owner can rename. Arrivals and visits are bus events, on the hub rather
than a camera: an arrival is a person, and a visit can now span two cameras.

State triggers fire on turning on only. They all clear themselves, and
firing again as somebody walks away is how an automation gets muted.

Bus events are filtered to their own hub, or a two-hub installation
announces the other house's front door. Hub and camera devices are both
identified as (DOMAIN, <something>) so the shape cannot tell them apart --
being a loaded hub can.

The phrases for the three house-wide triggers deliberately carry no
{entity_name}: it would resolve to the hub, and "Tapo H500 (192.168.11.5):
someone went round the house" is worse than the sentence alone.

## v0.72.0 &mdash; 2026-08-14

feat: a camera being handled is not a thirty-second event

Detection code 19 is the hub's own tamper alarm, confirmed by lifting the
front camera off its bracket at 11:16:16 on 2026-08-13. It is the only
detection here that is not about something outside the house, and if it is
real then the recordings after it are the ones that will be missing.

It had a binary sensor, which holds for thirty seconds and then clears --
right for a history graph, and useless for a fact somebody needs to see
whenever they next open Home Assistant. Nobody who was not already looking
ever knew.

So it now raises a repair issue as well: the camera, the local time, and how
many reports there have been in the last day, because once is a knock and
repeatedly is not. An ERROR rather than a warning, where the other five
issues are about footage being lost. Nothing dismisses it; it clears when
the report ages out of the window.

Matched against the events_1 mask rather than alarm_type, which reports only
the most significant code -- 20 outranks 19, so a camera lifted off its
mount while somebody the hub recognised stood there would otherwise have
reported as a face and nothing else. That is the shape of the real event.

The description says a knock or a gust can raise it too. Overstating this is
how a real alarm gets muted.

## v0.71.0 &mdash; 2026-08-14

feat: one arrival, however many cameras watched it happen

Two doorbells covering one path see the same person twice, so a visit event
per camera was two notifications about one arrival -- exactly what the visit
event exists to stop, reappearing a level up.

Two rules, both needed. Visits at different cameras within thirty seconds
are one arrival whoever it was; beyond that a shared face id is required as
well, because a person recognised at the gate and again at the door is
evidently one journey where two strangers two minutes apart are evidently
not. Never within one camera: its recordings are already grouped into
visits, and merging them again would swallow a real second visitor.

Two halves in the poll, because cameras almost never index a shared arrival
on the same one -- at two seconds apart they land on consecutive polls.
Visits from one poll are merged into a single event, and a visit matching
one just announced is suppressed. The memory of what was announced is pruned
to the longest window either rule looks at.

The event is keyed on where somebody was seen FIRST, which is where they
came from, and `cameras` lists everywhere that saw them -- always, so an
automation reading it never has to care whether one camera or two answered.

## v0.70.0 &mdash; 2026-08-14

feat: choose which detections are worth the disk

off / rings / all was the only choice, and on this firmware two of those
three were the same thing for months -- a TD21 doorbell labels every clip
video_type "2", so ring-only matched nothing and downloaded nothing until
code 17 was identified.

Even now it is a poor pair of options. A camera facing a road fills a drive
with traffic, and the clips people actually go back for are the ones with a
person or a press in them. Nine codes were identified the hard way and the
download path could only see two of them.

Stored as codes rather than labels, because the labels are a reading of what
the hub means and have been corrected once already. Empty is no filter,
which is what every existing installation has and what it keeps, and the
field is Optional so an untouched form can still be submitted.

It narrows the mode rather than replacing it: presses-only plus person
downloads presses that also had a person. Read live rather than added to
RELOAD_ON_CHANGE, so changing it costs no login to a hub that wedges under
repeated ones.

## v0.69.0 &mdash; 2026-08-14

feat: work the camera layout out instead of asking for it

The layout screen called this the one thing the integration could not work
out for itself. That was true of the hub, which reports no geometry, and
untrue of the recordings: people arrive from the street and walk towards the
door, so whichever camera sees somebody first is the one nearer the street.
The face trails have carried that since directions were added and nothing
read them.

Every hop between two cameras inside the direction window counts once -- a
point against the camera left, a point for the camera reached -- and the
order falls out of the totals. Ties break on the name so the answer does not
change between visits to the form.

It stays a suggestion. It fills in the defaults and nothing is written until
the form is submitted, anything already saved wins over it, and the form
says plainly that the numbers were inferred. Nothing at all is suggested
until somebody has actually been seen crossing between two cameras: a guess
with nothing behind it would become a default, and "approaching the door" is
what people wire a siren to.

coordinator.everyone() -- merged people plus the unnamed faces that cannot
be merged -- is factored out of the prowling sensor, which had built the
same list inline, and is what makes a journey visible when the gate and the
door were recognised as two different clusters of one person.

## v0.68.0 &mdash; 2026-08-14

feat: one person, however many times the hub clustered their face

The hub hands out a stable id per cluster and clusters the same person more
than once -- different light, a hat, a different angle. Giving both clusters
the same name is the only way to say they are one person, and nothing here
believed it: two sensors called Alice, two arrival events for one arrival,
and a trail split in half.

That last one was the real damage. Direction and prowling are read off the
trail, and half a trail holds one hop -- so gate on one cluster and door on
the other produced no direction from either half, and a circuit split across
two clusters was two journeys with no return in it.

people() merges by name and recomputes everything from the joined trail
rather than picking it from one part. The entity keeps the lowest id in the
group, so anyone the hub only ever clustered once is untouched and nothing
in the registry is orphaned. Arrivals now key on the person, the seen-
recently flag is true if either cluster saw them, and prowling reads merged
people plus the unnamed faces that cannot be merged.

face_ids carries every id in the group -- all of them, not just the ones
seen today, because an automation handed those ids has to match this person
tomorrow when a different cluster is the one the hub recognises.

## v0.67.0 &mdash; 2026-08-14

feat: the last hour in one word

Answering "is anything going on at the side gate" meant reading a recordings
count, an unusual-activity flag and a last-activity timestamp and joining
them up by eye -- three different questions, one conclusion, drawn
separately by every dashboard and every automation that wanted it.

sensor.<camera>_activity_level does the join once: quiet, active, busy or
unusual, judged against the camera's own recent rate with the same
per-camera sensitivity as the unusual flag.

busy is derived rather than given numbers of its own -- exactly halfway to
unusual, on both guards at once. Two independent pairs could be set so a
camera was busy at four events and merely active at five, and a scale that
goes backwards is worse than no scale.

unusually_busy now shares the threshold rather than restating it, so the
flag and the word are provably the same measurement.

## v0.66.0 &mdash; 2026-08-14

feat: count visitors, and show the shape of the day

recordings_24h answers a question nobody asked. The hub reports moments
rather than presence, so one person waiting four minutes at the door files
sixteen recordings -- a day reading "48 recordings" and a day reading "3
visits" can be the same day, and only one of those numbers means anything.

sensor.<camera>_visits_24h groups recordings the same way the loitering
sensor does, so the two cannot disagree about how many people were there.
Its attributes carry what a total throws away: `hourly`, 24 counts from
local midnight that a card can draw straight, and `longest_seconds`,
measured first sighting to last rather than to now.

hourly_counts is factored out of busiest_hour rather than written beside it,
so the peak and the chart are the same data reduced differently.

## v0.65.0 &mdash; 2026-08-14

feat: one notification per visitor, not one per recording

The hub reports moments rather than presence. Somebody standing at the door
for four minutes arrives as a string of fifteen-second clips, so an
automation wired to the detection event sends sixteen notifications about
one person -- which is how a useful signal becomes noise and gets muted.

sessions() has grouped clips into visits since the loitering sensor was
written and nothing was announcing that grouping. tapo_h500_visit now fires
once when a visit begins, carrying where, when, the alarm codes, the same
phrase the cards show, the face ids and the names of anyone recognised.

Fired at the start of the visit, which is the only moment a notification is
worth sending, and therefore knowing only about the first recording: at that
point somebody about to leave in ten seconds and somebody about to stay ten
minutes are indistinguishable. That is what the delivery and loitering
sensors are for, and why both are retrospective.

Silent until the first poll completes, like arrivals -- the window holds a
day, so a restart would otherwise announce every visit since breakfast.

describe_detection is split so the phrase can be built from codes: a visit
spans several recordings and has no single entry to describe, and the
alternative was hand-building an events_1 mask, which has already been got
wrong here once.

## v0.64.0 &mdash; 2026-08-14

feat: make two hubs work properly

Most of it already did: coordinators are keyed per config entry, every action
takes one, the card accepts an entry id and the spoken answers already walked
every hub. What did not work was everything keyed on a camera's NAME, and two
hubs can each have a "Front Doorbell".

The day's summary was a dictionary keyed on the alias, so one of the two was
dropped without a word and the answer described half the house as though it
were all of it. Names that clash are now qualified with their hub -- both of
them, not just the second, because "Front Doorbell" beside "Front Doorbell
(192.168.11.5)" makes the first look like the real one. Nothing changes for a
single hub, which is nearly everyone.

Downloads are filed under a slug of the camera's own name, and that is
deliberate: it makes "already downloaded" a check of the files on disk rather
than an index that could disagree with them. It also means two cameras sharing
a name share a folder, and that question is then answered for one by the
other's recording. Reported as a repair issue rather than worked around --
renaming a camera fixes it in seconds, and putting the hub into the path would
orphan every recording anyone has already downloaded to fix a case most
installations do not have. Compared as slugs, so aliases differing only in
case or spacing are caught; those are the ones nobody would spot.

The card's hub field is a picker rather than a box to type an opaque id into.
It only matters with more than one hub, which is exactly when nobody knows it.

## v0.63.0 &mdash; 2026-08-14

feat: set how busy is unusual, per camera

The baseline was already the camera's own rate, which covers a busy door and
a quiet gate seeing different amounts. What it could not cover was the two
meaning different things: three times typical is a Saturday on a doorbell
facing a pavement, and somebody in the garden on a back gate.

Three levels rather than the two numbers behind them. A multiplier against the
camera's own hourly average and a floor below which nothing is flagged are the
right model for the code and the wrong question to ask a person -- nobody
knows what multiple of its own average their front door reaches on a Saturday.

Normal is defined as the pair that has always been used, so an installation
configured before this existed behaves exactly as it did. A stored level this
version has never heard of falls back to it rather than raising, which would
take a camera's alarm away for a reason nobody could see.

Keyed by camera name, like the layout: an index shifts when a camera is
unpaired and a name does not. The sensor now also reports the multiplier and
the floor it is using, so "why has this not fired" is answerable from the
entity.

## v0.62.0 &mdash; 2026-08-14

feat: the day in one picture

A doorbell produces dozens of near-identical fifteen-second clips a day, and
looking through them means opening dozens of things. image.<camera>_today is a
contact sheet: every frame at once, small and in order, so the recording that
matters gets found by looking rather than by clicking.

Built with ffmpeg rather than Pillow. ffmpeg is already a dependency and
already makes every thumbnail here; the alternative was adding an image
library to lay out pictures ffmpeg can tile on its own.

The frames are staged as symlinks numbered from zero, because the image2
demuxer reads a numbered sequence and stops at the first gap -- the real
filenames are times of day and full of them. Symlinks rather than copies: a
dashboard asks for this picture repeatedly, and copying two dozen files each
time to feed a read-only process is work for nothing.

It redraws when a clip downloads rather than when the hub reports an event: a
sheet is made of thumbnails, and a thumbnail is written by the download, so
stamping on the event re-fetches an unchanged picture several seconds early.

ffmpeg is on the test machine, so the sheet is built and measured rather than
asserted about -- including that a frame it cannot read gives no sheet rather
than an empty one, which every caller would treat as a picture.

## v0.61.0 &mdash; 2026-08-14

feat: tell a delivery from a visit

Somebody was there, the hub did not recognise them, and they did not stay --
in daylight. That is a courier far more often than it is anything else, and
binary_sensor.<camera>_possible_delivery says so for five minutes afterwards.

Retrospective on purpose, and this is the whole of the design. At the moment
the hub reports a detection the person has been there for one clip, and so has
everybody about to stay for ten minutes: a visit's length is not knowable
while it is happening. Answering "is that a delivery at my door right now"
would mean saying yes about everyone who arrives, so it answers "was that a
delivery" and holds the answer long enough for an automation to see it.

It shares one visit helper with the loitering sensor, which is the same
measurement at the other end -- too short rather than too long. Two ways of
deciding what a visit is would let a run of clips be neither.

Named "possible" because nothing the hub reports says courier: a canvasser
looks identical, and so does somebody checking whether a house is empty. It is
a signal to describe an afternoon with, not a reason to stay quiet -- and at
night it never fires at all, which is the one wrong answer that would matter.

## v0.60.0 &mdash; 2026-08-14

feat: a blueprint that does something about it rather than telling you

A siren entity, a night signal and resolved names have all been here for a
while and nothing wired them together. respond_to_activity turns the lights
on, sounds the siren and says who is at the door.

Quiet out of the box, which is the whole design: it fires only for a face the
hub could not recognise, inside the night window the integration already
decides, and the siren is empty until somebody picks one. A siren that goes
off at three in the morning because a cat walked past is a siren that gets
unplugged, and this is the blueprint most able to cause that.

Announcing is the gentler half and stands alone. An unnamed face is "somebody
unrecognised" -- reading a twelve-digit id out to a room is worse than saying
nothing. Both the engine and the speaker must be set, because tts.speak names
them separately and fails the whole run if either is missing, which would take
the lights and the siren down with it.

One test here matched a comment above a condition that still said
detection_types while the code beside it had been changed to alarm_type. That
is the same trap that has caught assertions against docstrings and import
lines in this repo; the conditions are now read from the parsed document.

## v0.59.0 &mdash; 2026-08-14

feat: show what is inside a media browser folder

Clips already carried their own frame. The camera and date folders above them
were blank tiles, which is a poor way to find yesterday afternoon among thirty
identical grey rectangles.

Each folder now shows the newest thumbnail under it. Paths are
<camera>/<date>/<time>.jpg, so lexical order is chronological order and the
newest is the last of the last -- two directory scans rather than walking a
tree that holds thousands of files, on every browse. It walks back through
earlier days when the newest has no thumbnail at all, which happens for clips
downloaded before thumbnails existed.

Both of those are performance properties, and the first attempt at testing
them measured nothing: Path.iterdir and rglob both bottom out at os.scandir,
so counting the former made the cheap version and the whole-tree one look
identical, and the per-clip search bails at is_dir(), which is a stat rather
than a scan.

## v0.58.0 &mdash; 2026-08-14

feat: let the face names be taken out and put back

Face names and the camera layout are the only state here a hub cannot
reproduce. Recordings and settings live on the hub and every sensor is derived
from one of those; these two came out of somebody opening photographs to work
out who a twelve-digit number is. They live on the config entry, so deleting
the integration takes them with it and nothing warns first.

backup_names hands them back shaped to paste straight into restore_names.

Merging is the default, because the usual restore is an older backup onto an
entry that has learned a few more names since and replacing there discards
them silently. A blank name removes rather than stores, the rule the card and
the options screen already use. An absent camera_order leaves the layout alone
-- a backup taken before the layout existed must not read as "empty".

The merge rules live in their own module rather than inside the service
closure, so they can be run rather than grepped. Static assertions about a
closure are how the wholesale-options bug shipped in the first place, and
restored_options carries a test for exactly that: options are replaced entire
on save, so writing only the names deletes the poll interval.

SERVICES had also collected a signal name, an option key, a prompt string and
a tuple. Nothing broke -- has_service just answered no -- so nothing noticed,
and it made the list useless as a statement of what gets cleaned up.

## v0.57.0 &mdash; 2026-08-14

feat: keep the recordings with a person in them, and fix which presses survived

Retention protected doorbell presses already, but not correctly: it sliced the
clip list in whatever order the hub returned it. searchVideoWithUTC promises
no order, and on a hub answering oldest-first that protected exactly the
recordings about to be deleted anyway -- so the feature did nothing and said
nothing. Sorting by start time is the fix.

Recordings with a person now get their own count alongside presses. Two
classes and deliberately only two: nine detection codes exist, and a number
for each would be nine boxes on a form expressing one idea. Motion is a cat,
vehicles are the road, and the face codes never fire without the person code
beside them.

The two counts are independent, so ten presses cannot use up the allowance for
people, and a press with a person in it lands in both sets without being
protected twice.

keep_person was missing from RELOAD_ON_CHANGE, which would have left the
coordinator holding the old figure until something else forced a reload. A
test caught that rather than a user.

## v0.56.0 &mdash; 2026-08-14

feat: put every detection on a calendar

Home Assistant already has a panel built for "what happened last Tuesday", and
the cards do not answer that without knowing to look at Tuesday first. One
calendar per camera, each entry naming what the hub detected and who it
recognised, with the face ids of anyone unnamed in the description.

The entries come from the hub rather than the polled window. That is the whole
design decision: the coordinator holds a day, so a calendar built on it would
show one day and then nothing, and scrolling back would suggest a quiet
fortnight rather than an absent one. The hub keeps weeks and answers a
detection search in about 17ms, so one lookup per view is cheap.

Bounded at 31 days per view because searchDetectionList caps at 1000 records:
an unbounded year view returns a fraction of the year and does not say so.

A hub that refuses gives an empty view rather than an exception. The calendar
panel is not where a hub fault should surface, and one that throws takes the
whole panel with it.

## v0.55.0 &mdash; 2026-08-14

feat: tell going round the house from walking up to the door

A visitor passes each camera once. Somebody circling comes back to one they
have already been past, and that return is the entire signal.

Which means it needs no camera layout, unlike direction: it does not matter
which camera is nearer the street, only that the same place was reached twice
with somewhere else in between. So it works on a hub whose layout has never
been filled in, and it works with two cameras -- front, side, front is a
circuit, and requiring three distinct places would make it unreachable on the
hardware this was written for.

binary_sensor.<hub>_prowling, on the hub rather than a camera, because a
circuit is by definition not about one camera. The faces attribute says who.

Consecutive sightings at one camera collapse first, so three clips of somebody
waiting at the front door is not a lap -- that is the loitering sensor -- and
the ten-minute window keeps this morning's visit and this evening's from
looking like one circuit.

No separate checks for two distinct cameras or three hops: a repeat in the
collapsed path implies both, and a guard that cannot fire is one no test can
protect.

## v0.54.0 &mdash; 2026-08-14

feat: mute notifications for an hour without disabling the automation

There was no way to stop the phone buzzing except turning the automation off,
which is the thing people forget to turn back on. An afternoon of gardening or
a party is worth an hour of quiet; it is not worth a doorbell that stays
silent for a week.

switch.<hub>_notifications_snoozed, plus a tapo_h500.snooze action taking a
duration -- switch.turn_on has nowhere to put one. The notification blueprint
gets a Snooze switch input, defaulting to empty so nothing changes on upgrade.

It is a flag, not a filter. The poll never consults it: recording, downloading
and events all carry on, because footage taken during a snooze is the footage
most likely to be wanted afterwards. There are tests asserting the poll path
does not mention it.

Nothing is written to disk, deliberately. A snooze that outlived a restart
would be a silent doorbell nobody remembered turning off. Expiry needs no
timer either -- every entity reading it redraws on the poll, which is every
couple of seconds.

## v0.53.0 &mdash; 2026-08-14

feat: say how long before the hub starts overwriting

Full is not a failure on this hardware -- loop recording discards the oldest
footage silently rather than stopping -- which is exactly why a warning at
100% is too late. sensor.<hub>_storage_full_in is the deadline for downloading
anything worth keeping.

There is no history to read. The hub reports how full it is and nothing about
how full it was, so the rate is fitted to samples taken while Home Assistant
runs, one per status refresh from the round trip that already happens every
minute. Least squares over the run rather than first-versus-last, because the
hub rounds to a tenth of a percent and that makes the endpoints the two least
reliable points to build a line from.

It reports nothing rather than a number whenever it does not know: under an
hour of history, or a disk that is not filling. And the history restarts when
the figure falls, which is what a format, a swapped card and loop recording
catching up all look like -- fitting a line across that drop would forecast
from a slope that never happened.

Two guards came back out. Checking for fewer than two samples and for a zero
spread in the timestamps could neither of them fire once the span check
existed, so no test could tell a broken one from a working one.

## v0.52.0 &mdash; 2026-08-14

feat: notice a camera that has stopped recording anything

A camera off the Wi-Fi, flat or unplugged is invisible: every entity keeps
showing its last value, and the usual way to find out is needing the footage
and not having it.

The hub offers nothing to check instead. Its paired-device record has 16
fields and not one is an online flag, a signal strength or a battery -- the
protocol notes already record that, alongside the eleven battery methods that
all answer -40106. Silence is the only evidence there is, so the entity is
called Silent rather than Offline.

binary_sensor.<camera>_silent, plus a repair issue, plus an adjustable
threshold because a back gate that genuinely sees nobody all day is not a
fault. The threshold caps at 24 hours: the hub is asked for a day of
recordings, so "nothing in three days" is not a question it can answer, and
offering it would make a sensor that never turns on for an invisible reason.

Unknown rather than off before the first poll completes. An empty list there
means "not asked yet", and reporting every camera silent on startup would be
an alarm about the integration rather than the hardware.

## v0.51.0 &mdash; 2026-08-14

feat: notice someone standing at the door rather than passing it

Nothing here could tell a four-minute wait from a walk-past. The busy-camera
flag is a rate over an hour and the night flag is about the clock; both read
the two the same. The missing piece is duration, and the hub never reports it
-- it reports moments, so a visit has to be reassembled from the clips.

sessions() groups recordings less than two minutes apart into one visit.
binary_sensor.<camera>_loitering is on while an unrecognised face has been in
one for over three minutes, with the duration as an attribute.

Measured from the first sighting to the last rather than to now. Counting the
silence since would push every brief visit over the threshold the moment it
ended, and every camera would loiter. A recognised face never triggers it,
however long they wait at their own door.

## v0.50.0 &mdash; 2026-08-14

feat: tell arriving home apart from walking past again

The detection event fires every time anyone crosses a camera, which is right
for a doorbell and the wrong grain for a household: someone working from home
trips the front camera a dozen times a day and only the first is news.

tapo_h500_arrival fires once per named person per local day. Named only --
an unnamed id appearing is a stranger, which the ordinary event already
reports, and "Face 481036337152 has arrived" helps nobody.

Two things needed care. The check runs inside the poll that fetched the
recordings rather than against the published copy, because the window holds a
day of clips and reading the previous poll's data turns everyone seen this
morning into a fresh arrival on the second poll after a restart. And the day
is the local one: at half past ten at night in any zone west of Greenwich, a
UTC day boundary puts an arrival on tomorrow.

The test harness now runs in a fixed -07:00 rather than the machine's zone.
On a UTC box "local" and UTC agree, so a day computed in UTC by mistake
passed every test -- it does not now.

## v0.49.0 &mdash; 2026-08-13

0.49.0

Statistics, a people card, hub health, and seen-recently sensors.

## v0.48.0 &mdash; 2026-08-13

0.48.0

Backoff from a failing hub, download verification, and clip export.

## v0.47.0 &mdash; 2026-08-13

0.47.0

Name faces from the phone, night escalation, and search by person.

## v0.46.0 &mdash; 2026-08-13

0.46.0

A recognised person's trail now reads as approaching or leaving.

## v0.45.0 &mdash; 2026-08-13

0.45.0

Faces the hub keeps seeing are suggested for naming.

## v0.44.0 &mdash; 2026-08-13

0.44.0

Follow a recognised person between cameras.

## v0.43.1 &mdash; 2026-08-13

0.43.1

Naming a face no longer reloads the integration or costs a hub login.

## v0.43.0 &mdash; 2026-08-13

0.43.0

Faces can be named from the card.

## v0.42.1 &mdash; 2026-08-13

0.42.1

The face photo link resolves, and opens in a new tab.

## v0.42.0 &mdash; 2026-08-13

0.42.0

Notifications name recognised people.

## v0.41.0 &mdash; 2026-08-13

0.41.0

Face naming links to each face's photograph.

## v0.40.0 &mdash; 2026-08-13

0.40.0

Face naming moves into the integration's options, and saving options no
longer wipes the names.

## v0.39.0 &mdash; 2026-08-13

0.39.0

LLM clip captioning, an optional daily summary, retention that protects
doorbell presses, and Assist answers.

## v0.38.0 &mdash; 2026-08-13

0.38.0

Diagnostics, logbook entries, repair issues and an image entity.

## v0.37.0 &mdash; 2026-08-13

0.37.0

Per-camera unusual-activity detection, judged against each camera's own
recent rate.

## v0.36.0 &mdash; 2026-08-13

0.36.0

Face names move to the hub's config entry, shared by every card, and each
named face gains a last-seen sensor.

## v0.35.0 &mdash; 2026-08-13

0.35.0

A binary sensor per detection per camera, including an unfamiliar-face
sensor, giving detections a history and usable conditions.

## v0.34.0 &mdash; 2026-08-13

0.34.0

An importable blueprint replaces the copy-and-edit notification example.

## v0.33.0 &mdash; 2026-08-13

0.33.0

Every detection the hub reports is now a device trigger in the automation
editor.

## v0.32.1 &mdash; 2026-08-13

0.32.1

The summary card's table no longer overflows the card or hides the button
that switches back to the chart.

## v0.32.0 &mdash; 2026-08-13

0.32.0

Names alarm_type 10 as a doorbell press nobody answered, and collapses it
with 17 so a press is described once rather than twice.

Also since 0.31.0: events carry a thumbnail pinned to their own timestamp
rather than whatever is newest, a seventh card charts faces by sighting
count, and the docs no longer claim codes are unnamed.

## v0.31.0 &mdash; 2026-08-13

0.31.0

Notifications show the frame from their own event. New card: faces by
sighting count.

## v0.30.0 &mdash; 2026-08-13

0.30.0

Doorbell presses that follow motion now raise an event. Notifications
arrive instantly and are replaced by one carrying the correct frame.

## v0.29.0 &mdash; 2026-08-13

0.29.0

The poll interval can be set while adding a hub, not only afterwards.

## v0.28.0 &mdash; 2026-08-13

0.28.0

Notifications land about a second after the hub sees an event, down from
up to twenty. Poll interval 2s, camera list cached, hub status off the
hot path.

## v0.27.0 &mdash; 2026-08-13

0.27.0

Cuts notification latency: hub status no longer blocks detection
lookups, and the poll interval drops from 20s to 10s.

## v0.26.0 &mdash; 2026-08-13

0.26.0

Adds a ready-made notification automation for a person, an animal or the
doorbell, covering every camera on the hub from a single automation and
naming which detection happened at which camera.

Fixes the README header image, which used a relative path and so could
not resolve when HACS rendered the page.

## v0.25.0 &mdash; 2026-08-13

0.25.0

Face names editable in the visual editor.

## v0.24.0 &mdash; 2026-08-13

0.24.0

Code 22 identified as an unrecognised face; eight of nine codes named.

## v0.23.0 &mdash; 2026-08-13

0.23.0

alarm_type 19 identified as a theft alarm; seven of nine codes now named.

## v0.22.1 &mdash; 2026-08-13

0.22.1

Narrow timeline cards no longer clip the event time.

## v0.22.0 &mdash; 2026-08-13

0.22.0

Event codes decoded: person, vehicle, pet, doorbell, face and motion.

## v0.21.0 &mdash; 2026-08-13

0.21.0

Doorbell presses identified: alarm_type 17, confirmed against a real
press. Plus a visual editor for all six cards.

## v0.20.0 &mdash; 2026-08-13

0.20.0

Visual editor for all six cards.

## v0.19.0 &mdash; 2026-08-13

0.19.0

Original icon and header lockup in brand/, sized for home-assistant/brands.

## v0.18.0 &mdash; 2026-08-13

0.18.0

Resizable cards via getGridOptions, each sized to what it needs, plus
confirmed one-camera pinning across all six cards.

## v0.17.0 &mdash; 2026-08-13

0.17.0

A summary card: events by hour of the local day as a bar chart, with a
table twin and theme-driven colours.

## v0.16.0 &mdash; 2026-08-13

0.16.0

A faces card: one tile per person the hub recognised, with their newest
picture, a sighting count and a name you supply. The hub assigns a stable
face id and keeps names and photos in the cloud, so the card supplies the
missing half locally.

## v0.14.0 &mdash; 2026-08-13

0.14.0

Face detection becomes a switch: -40211 was a parameter complaint, not a
refusal. setFaceDetectionConfig accepts only the whole detection block,
so the toggle sends the tag list back with every write.

## v0.13.0 &mdash; 2026-08-13

0.13.0

Hub clock offset, timezone and custom sound slots exposed. The clock
offset is signed and measured against Home Assistant's own clock, because
clip filenames and media-browser date folders come from hub timestamps.

## v0.12.0 &mdash; 2026-08-13

0.12.0

Probed all 47 hub components; face detection exposed as a diagnostic
sensor. The hub wants the camera account username (admin), not the
TP-Link cloud email -- its refusal reads exactly like a lockout.

## v0.11.0 &mdash; 2026-08-13

0.11.0

Fixes evening recordings vanishing from the card: a clip at 21:16 on a
hub at UTC-4 was never listed, because the hub reports the dates it holds
video for in its own local time and those were read back as UTC dates.

The event entity was never affected.

## v0.10.0 &mdash; 2026-08-13

0.10.0

Siren and hub settings control, four dashboard cards, thumbnails for clips
that are still only on the hub, and recordings that report what actually
triggered them from the hub's detection log.

First tag since v0.5.0.

## v0.5.0 &mdash; 2026-08-13

Download retention limit and a scrollable card.

## v0.4.2 &mdash; 2026-08-13

Register the dashboard card once.

## v0.4.1 &mdash; 2026-08-13

Fix MP4 conversion, which had never worked, and scale thumbnails down.

## v0.4.0 &mdash; 2026-08-12

One card covers every paired camera.

## v0.3.1 &mdash; 2026-08-12

Register the dashboard card as a Lovelace resource so it actually loads.

## v0.3.0 &mdash; 2026-08-12

Sensor and binary_sensor platforms.

Hub storage free/total/used and health, siren state and countdown, firmware
state and IP. Per camera: last activity, 24h recording count, and the
paired-device flags. All in one batched request per poll.

No battery level; no battery getter works on an H500.

## v0.2.0 &mdash; 2026-08-12

Events, automatic download, thumbnails, MP4 conversion, media browser and dashboard card.

Live view is stills only; the H500 exposes no live/preview/stream module.
Includes the empty-nonce fix required by hub firmware 1.3.20, without which
every download fails.
