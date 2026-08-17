# Reference

Full entity, card and action reference for the Tapo H500 integration. The
[README](../README.md) covers installing and configuring it; this file is what
you look things up in afterwards.

## Entities

The hub gets its own device, and each paired camera gets one.

**Hub**

| Entity | Use |
| --- | --- |
| `sensor.*_storage_free` / `_storage_total` / `_storage_used` | Recording space, in GB and percent. Automate a warning before the loop starts overwriting. |
| `sensor.*_storage_status` | The hub's own word for the disk state, e.g. `normal`. |
| `binary_sensor.*_storage_problem` | On when the disk is not `normal`. |
| `siren.*` | Sounds the hub siren, and reports whether it is sounding. Supports a tone (19 sounds, from Doorbell Ring 1 to Alarm 5), a volume and a duration; the current settings are attributes. |
| `sensor.*_siren_time_left` | How much longer the siren will sound. |
| `sensor.*_firmware_state`, `sensor.*_ip_address` | Diagnostics. |
| `binary_sensor.*_media_encryption` | Diagnostics. |
| `sensor.*_clock_offset` | Seconds the hub's clock differs from Home Assistant's, signed. Clip filenames and the media browser's date folders come from hub timestamps, so drift files recordings under the wrong day. |
| `sensor.*_timezone` | The hub's own timezone. |
| `sensor.*_custom_sounds` | How many of the hub's five custom sound slots hold a recording, with their names as an attribute. |
| `sensor.*_auto_upgrade_time` | The hour the hub installs firmware, with `enabled` and the `random_range` window it spreads updates over. The switch says *whether*; this says *when*, and a hub that reboots itself to update mid-afternoon is worth knowing about first. |
| `sensor.*_scheduled_reboot` | `off`, or the time the hub reboots itself. Read only — `setReboot`'s params are ambiguous between scheduling a reboot and performing one. Unknown rather than `off` if the hub does not answer. A reboot schedule explains a gap in recordings that the silent-camera watchdog would otherwise call a broken camera. |
| `sensor.*_people_seen_recently` | How many of the people you have named were seen in the last ten minutes, with `seen_recently`, `seen_today`, `not_seen` and `named` as attributes. One entity per person is right for automating and wrong for looking at; this is the same information in one place. `not_seen` is people who have **not been seen** — a camera watches a doorstep, not a house, so it is not a list of people who are out. |

**Hub settings you can change**

Every control below was confirmed writable on firmware `1.3.20` by writing the
hub's own current value back to it and checking the hub accepted it without
anything moving.

| Entity | Effect |
| --- | --- |
| `switch.*_status_led` | The hub's status light. |
| `switch.*_loop_recording` | Whether the hub overwrites the oldest footage once storage fills. Turning it off means recording stops when full. |
| `switch.*_automatic_firmware_updates` | The hub's own auto-update. Toggling keeps the update time the hub already holds. |
| `switch.*_face_detection` | The hub's face detection. Toggling keeps the recognised tags (family, friend, courier, neighbour, colleague, schoolmate, others) — the hub refuses a change that omits them. |
| `switch.*_diagnostic_mode` | TP-Link's diagnostic logging. Off unless you are chasing something. |
| `select.*_siren_sound` | Which of the 19 sounds the siren uses, set without sounding it. |
| `number.*_siren_volume` | 1-10. The hub refuses anything outside that. |
| `number.*_siren_duration` | How long the siren sounds, in seconds. |

The read-only `binary_sensor.*_siren`, `_led` and `_loop_recording` entities are
**gone**: the siren and switch entities above carry the same state and can also
change it, and a read-only twin of each meant two entities per fact. Delete them
from any dashboard that still lists them. `binary_sensor.*_media_encryption`
stays, because that one is deliberately not writable.

Three settings the hub exposes are deliberately **not** offered: the reboot
schedule (its call is ambiguous between scheduling a reboot and performing one),
media encryption (turning it off would break the verified download path) and the
timezone (changing it would shift every clip timestamp).

**Per camera**

| Entity | Use |
| --- | --- |
| `camera.<name>` | The frame from that camera's newest clip. Fetched from the hub if no download has written it yet — so the notification's Camera button shows the event it announced, not the previous one. While the hub is still recording, the previous event is all that exists anywhere. |
| `event.<name>_activity` | Fires `ring` or `motion`, with `start_time`, `end_time`, `duration` and the hub's raw `hub_type` label as attributes. |
| `sensor.<name>_last_activity` | Timestamp of the newest recording. Drives "nothing seen since" automations. |
| `sensor.<name>_recordings_24h` | How many clips the hub holds for this camera. |
| `sensor.<name>_visits_24h` | How many separate **visitors**, which is a much smaller number: the hub reports moments, so one person waiting four minutes at the door files sixteen clips. Attributes: `hourly` (24 counts from local midnight, for a chart), `longest_seconds`, and the `gap_seconds` used to group them. |
| `sensor.<name>_activity_level` | The last hour in one word: `quiet`, `active`, `busy` or `unusual`. Judged against the camera's own rate with the same sensitivity setting as the unusual flag, and `busy` is exactly halfway to `unusual` rather than a separate pair of numbers — so the scale cannot go backwards. Attributes say what it was measured against. |
| `sensor.<name>_ai_enhance`, `_network_mode`, `_model` | Diagnostics. |
| `binary_sensor.<name>_hub_storage`, `_24_7_recording`, `_ai_enhance_enabled`, `_wifi_backup` | Diagnostics. |

Everything above comes from one extra `multipleRequest`, batched into a
single round trip because this hub is easy to overload.

The hub device itself carries a **Firmware** and **Hardware** version, read
from the `getDeviceInfo` reply pytapo already fetches during login — no extra
call, and it cannot change while the integration is loaded.

**No battery level.** None of the battery getters work on an H500 — the reading
lives on the camera, and the hub exposes no way to address a camera child. See
`protocol-notes.md`.

Cameras are enumerated when the config entry loads. Pair a new camera, then
reload the integration to pick it up.

**Doorbell presses are not distinguishable yet, but finding the code is now a
one-minute job.** Nothing classifies as a `ring`, so the presses-only download
mode still matches nothing.

Every event and every listed recording now carries what the hub reported:

| Attribute | Example |
| --- | --- |
| `detection` | `motion + face`, or `type 22` for a code with no name |
| `alarm_type` | `22` — the most significant code |
| `detection_types` | `[2, 6, 9, 22]` — everything that fired at once |
| `face_ids` | `[272465657857]` — one number per recognised face |

The hub gives a **number per face and nothing else**: no name and no picture,
and no library to resolve the number against — `getFaceList`, `getFaceInfo`,
`searchFaceList` and `getFaceLibrary` are all absent, and the accompanying
`face_bitmap` has been `0` on every detection seen, so it categorises nothing.

The number still earns its place, because the same person appears to keep the
same id. Name them yourself:

```yaml
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >-
            {% set who = {272465657857: 'Alice', 1969491410946: 'the courier'} %}
            {% set ids = state_attr(trigger.entity_id, 'face_ids') or [] %}
            {{ who.get(ids[0], 'Someone') if ids else 'Motion' }} at the door
          data:
            image: /api/camera_proxy/camera.side_doorbell
```

To *see* the face, use the picture rather than the id — the clip thumbnail is
the actual frame, and it is available before the clip is downloaded.

To pin the press: press the doorbell, look at `alarm_type` on the
`event.<camera>_activity` entity that follows, and add that number to
`RING_ALARM_TYPES` in `const.py`. The event entity, the presses-only download
filter and all four cards read the same list, so one number fixes every path.

### Responding instead of notifying

A second blueprint,
[`respond_to_activity.yaml`](../blueprints/automation/tapo_h500/respond_to_activity.yaml),
turns the lights on, sounds the siren and says who is at the door. Every piece
was already here; nothing wired them together.

Conservative out of the box: it fires only for a face the hub could **not**
recognise, inside the night window from the integration's own options, and the
siren stays silent until you pick one. A siren that goes off at three in the
morning because a cat walked past is a siren that gets unplugged.

Announcing is the gentler half and works alone — "Alice is at the front door"
over a speaker, using the names you gave the hub's face ids. An unnamed face is
"somebody unrecognised"; reading a twelve-digit number out to a room is worse
than saying nothing.

It respects the snooze switch, skips the state an event entity restores on
restart, and runs one response at a time.

### Notification automations

A ready-made one is in
[`examples/notify-person-pet-doorbell.yaml`](../examples/notify-person-pet-doorbell.yaml):
one automation covering both cameras that fires only for a person, an animal or
the doorbell, and says which happened at which camera --

> **Someone rang the Front Doorbell**
> Saw a doorbell press, a person, an animal and motion, 5:41 PM

Most events carry several codes at once, so the headline picks one by priority
-- doorbell, then person, then animal -- and the body lists everything else the
hub saw. The camera's own frame is attached, tapping the notification opens
that camera's dialog, and a Recordings button opens the media browser. Once
the photograph arrives, a **Save clip** button appears beside them: one press
downloads that exact recording, and a manual download is never pruned — "keep
that one forever" from the lock screen. Plain motion never notifies on its own but is still described when it
arrives alongside something that does. Change `notify.mobile_app_phone` to your
own notify service; nothing else needs editing for a two-camera hub.


Trigger on the **entity**, not on its `event_type` attribute. An event entity's
state is the timestamp of the last event, so it changes every time; the
attribute does not change between two events of the same kind, so an attribute
trigger silently misses the second one.

Notify on any activity, with the camera's own frame attached:

```yaml
automation:
  - alias: Camera activity
    triggers:
      - trigger: state
        entity_id: event.side_doorbell_activity
    conditions:
      # Skip the entity restoring its last event on restart or reload.
      - condition: template
        value_template: >-
          {{ trigger.from_state.state not in ['unknown', 'unavailable']
             and trigger.to_state.state not in ['unknown', 'unavailable'] }}
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >-
            {{ state_attr(trigger.entity_id, 'detection') or 'Activity' }}
            at {{ trigger.to_state.name }}
          data:
            # The camera entity serves the newest event's frame.
            image: /api/camera_proxy/camera.side_doorbell
```

Notify only for one kind of detection — here the code that carries a face ID:

```yaml
    conditions:
      - condition: template
        value_template: >-
          {{ 20 in (state_attr(trigger.entity_id, 'detection_types') or []) }}
```

`detection_types` lists **everything** that fired at once, so testing it catches
a code even when a more significant one is what `alarm_type` reports. Use
`alarm_type` instead when you want only the headline type:

```yaml
      - condition: template
        value_template: "{{ state_attr(trigger.entity_id, 'alarm_type') == 22 }}"
```

Once a doorbell press has been identified and its code added to
`RING_ALARM_TYPES`, presses classify as `ring` and the condition becomes:

```yaml
      - condition: template
        value_template: >-
          {{ state_attr(trigger.entity_id, 'event_type') == 'ring' }}
```

The recording downloads on its own; the clip and its thumbnail land under
**Media → Tapo H500**.

## Dashboard card

The integration registers the card as a dashboard resource on startup, so
there is normally nothing to add by hand. If a dashboard still reports
`Custom element doesn't exist: tapo-h500-card`:

1. Confirm the file is served: open `/tapo_h500_static/tapo-h500-card.js` in a
   browser. If that 404s the integration did not load — check the log.
2. Hard-refresh the page (Ctrl+Shift+R). A normal reload does not re-read the
   resource list.
3. Failing that, add it by hand under **Settings → Dashboards → three-dot menu
   → Resources**: URL `/tapo_h500_static/tapo-h500-card.js`, type **JavaScript
   Module**.

Automatic registration needs a storage-mode dashboard. YAML-mode dashboards own
their own resource list, so add the resource there yourself.

Four cards ship in that one resource, so registering it makes all of them
available in the card picker. They show the same recordings different ways:

| Card | Shows |
| --- | --- |
| `custom:tapo-h500-card` | A list, one row per clip, with download, play and delete. The one to browse and manage with. |
| `custom:tapo-h500-hero-card` | Only the newest event, large, with "2 minutes ago" and a tap to play. For a wall tablet or the top of a dashboard. |
| `custom:tapo-h500-grid-card` | Every clip as a thumbnail tile. Fits far more events on screen for scanning a busy day. |
| `custom:tapo-h500-timeline-card` | Clips grouped under hour headings, so the gaps in a day are visible. |
| `custom:tapo-h500-faces-card` | One tile per person the hub recognised, with their newest picture, how many times they were seen, and the name you give them. |
| `custom:tapo-h500-summary-card` | A bar chart of events by hour of day, so you can see when things actually happen. |
| `custom:tapo-h500-face-summary-card` | A bar chart of how often each face was seen, ranked most-seen first. |

Add a manual card:

```yaml
type: custom:tapo-h500-card
days: 2
max_height: 400
```

### The summary card

Events by hour of the local day, as a bar chart — when things actually happen,
rather than what happened.

```yaml
type: custom:tapo-h500-summary-card
days: 7
```

One bar per hour, one colour for all of them: shading bars by height would
re-encode the length the bar already shows. The scale starts at zero and tops
out at a round number, so the tallest bar is read against a number you can
halve by eye. Only the busiest hour carries a printed value; hovering any hour
gives its count, and the hover target is the full column rather than a bar that
may be three pixels tall.

The **Table** button swaps the chart for the same 24 numbers. A value that
exists only inside a tooltip is a value some readers cannot reach, so the chart
always has a text twin.

Colours come from your Home Assistant theme, so it follows light and dark mode
rather than shipping its own palette.

### The face summary card

How often each face was seen over the period, as a horizontal bar chart ranked
most-seen first.

```yaml
type: custom:tapo-h500-face-summary-card
days: 7
names:
  123456789012: Alice
```

Horizontal rather than vertical because the categories are names of arbitrary
length; vertical bars would have to rotate or truncate them. The bars are
sorted by count with a stable tiebreak, so two faces seen the same number of
times do not swap places between redraws. Every count is also in a table below
the chart, so nothing is available only on hover.

Takes the same `names` map as the faces card — the hub supplies ids, never
names. Anyone unnamed shows as `Face <id>`, which is the id to add to the map.

### The faces card

The local answer to the app's recognised-faces summary. The hub recognises but
will not identify — it assigns a stable id per person and keeps the name and
photo in TP-Link's cloud, where no local call reaches them. This supplies the
missing half: the picture comes from that person's newest clip, and the name
comes from you.

```yaml
type: custom:tapo-h500-faces-card
days: 7
names:
  272465657857: Alice
  1969491410946: Courier
```

Without a `names` map every tile reads as `Face 272465657857`, and the card
tells you which id to add. Build the map by watching which id appears when you
know who was there. Ids not in the map still appear — they are simply unnamed,
so a stranger is never hidden.

Only recordings the hub attached a face to are listed; motion-only clips are
not people.

All six take the same options.

**Editing.** Every card has a visual editor, so picking one from the card list
gives a form rather than "no visual editor available". The form covers the days
to show, which camera to pin to, when to start scrolling and which hub to use.
The faces card also gets a **Face names** field, so the id-to-name map can be
edited in the UI instead of only in YAML — the card prints the id of anyone
unnamed, so it can be copied straight across. `grid_options` has no field
because dragging writes it, and editing in the UI leaves it untouched rather
than dropping it.

**Resizing.** In a **sections** dashboard every card has drag handles — grab an
edge and it keeps the size you choose. Each card starts at a size that suits it
and refuses to be squashed past the point where it stops being readable: the
hero card keeps enough rows for its picture, and the summary chart keeps enough
columns for 24 hourly bars. In the older **masonry** layout there are no
handles, so `max_height` is still how you cap a long list there.

**One camera per card.** Without `camera_index` a card shows buttons for every
paired camera and remembers which you picked. Setting it pins the card to that
camera and hides the picker, which is what you want for one card per doorbell:

```yaml
type: custom:tapo-h500-hero-card
camera_index: 0     # 0 is the first paired camera, 1 the second
grid_options:       # written for you when you drag, or set it by hand
  rows: 8
  columns: 6
```

`max_height` still caps the scrolling cards, but a card you have resized
ignores the *default* cap — otherwise dragging one taller would strand blank
space under a short list. An explicit `max_height` is you asking, so it still
wins.

Every card only offers **Play** for clips already downloaded; a clip still on
the hub shows **Download** instead. All of them show a thumbnail either way.

With no `camera_index` the card shows a button per paired camera and remembers
which one you picked, so one card covers the whole hub. Setting `camera_index`
pins it to a single camera and hides the picker, which is what you want if you
prefer one card per doorbell. `days` defaults to `1`. `entry_id` is optional and
only needed if you run more than one H500. `max_height` is the pixel height at
which the list starts scrolling instead of stretching the dashboard; set `0` to
let it grow.

Each row shows the thumbnail, the local time, the event type and the duration,
plus **Download** for clips still only on the hub and **Play**/**Delete** for
clips already downloaded.

Rows for clips that are still only on the hub show a thumbnail too. The hub's
download session takes a time window, so a preview does not need the whole
recording — the integration pulls the opening couple of seconds, keeps one
frame and throws the video away. Measured on firmware `1.3.20` that is about
230 KB and two seconds, against roughly 3.4 MB for a full 15-second clip.

Previews are made only when something actually asks for the image, and the card
marks its images `loading="lazy"`, so scrolling a long list does not fetch
anything you never looked at. Each one is cached on disk at the same path the
downloaded clip's thumbnail would use, so it is generated once, and downloading
that clip later finds it already there.

## Actions

All four actions return a response and appear under **Developer tools → Actions**.

### `tapo_h500.list_recordings`

```yaml
config_entry_id: YOUR_CONFIG_ENTRY_ID
camera_index: 1
start_date: "20260812"
end_date: "20260812"
```

Dates use `YYYYMMDD` in UTC and default to today. Each returned recording
carries exact `start_time`/`end_time` boundaries, a `duration`, the classified
`event_type`, the hub's raw `video_type`, and `downloaded`. Already-downloaded
recordings also carry `url`, `thumbnail`, `path` and `media_content_id`.

### `tapo_h500.download_recording`

```yaml
config_entry_id: YOUR_CONFIG_ENTRY_ID
camera_index: 1
start_time: 1786553183
end_time: 1786553198
```

`end_time` may be left out: the integration asks the hub which indexed
recording starts at `start_time` (one second of tolerance) and uses its real
end — which is how the notification's **Save clip** button works, since the
detection log never carries an end. With both times given, copy them exactly
from `list_recordings`. `convert_to_mp4` optionally overrides the
integration option for a single download.

### `tapo_h500.delete_recording`

```yaml
config_entry_id: YOUR_CONFIG_ENTRY_ID
camera_index: 1
start_time: 1786553183
```

Removes the downloaded clip and its thumbnail. The hub keeps its own copy.

### `tapo_h500.format_hub_storage`

```yaml
config_entry_id: YOUR_CONFIG_ENTRY_ID
confirm: true
```

**Destroys every recording on the hub.** `confirm: true` is required and there
is no undo.

## Face names

The hub clusters faces and gives each a stable id, but will not say who anyone
is — there is no face library to look an id up in. Names come from you.

**The usual way: Settings → Devices & services → Tapo H500 → Configure → Name
faces.** Every face the hub has recognised is listed with how many times and on
which cameras it saw them, a link to that sighting's own photograph, and a box
to type a name into. Open the photo to see who the number belongs to. A face
has no link until its recording has downloaded — the thumbnail is written by
the download, so someone seen a minute ago will not have one yet. Clearing a box
removes the name. Faces you have already named stay on the list even once they
stop appearing, so a name can be corrected rather than only added.

**From the faces card:** every face has a **Name this face** button under it
(**Rename** once named). It writes to the same place the options screen does,
so the two never disagree. Cancelling changes nothing; emptying the box clears
the name.

For automations, the same thing as an action:

```yaml
action: tapo_h500.name_face
data:
  config_entry_id: <your hub>
  face_id: "123456789012"
  name: Alice
```

Stored once on the config entry, so every card and every per-face sensor uses
it. Leave `name` empty to clear it. Cards keep their own optional `names:`,
which overrides the shared map for that card only.

Naming someone creates two sensors on the hub device: `sensor.<name>` holding
when they were last seen, and `sensor.<name>_last_seen_at` holding which camera
saw them.

**Give the same name to more than one id and they become one person.** The hub
clusters the same face more than once — different light, a hat, a different
angle — and there is no way to tell it they are the same. Naming both is that
way. From then on they share one pair of sensors, one `binary_sensor` and one
arrival event, their sighting counts add up, and their trails are joined before
the direction and prowling checks run. That last part is the point: gate on one
cluster and door on the other is a direction only once they are the same
person, because one hop is never a direction.

The entity keeps the lowest of the shared ids, so anyone the hub only ever
clustered once is untouched. `face_ids` on each sensor lists every id in the
group; `face_id` stays the one that saw them last, which is the one whose
photograph exists.

The second is what follows a person around the house. Face ids are hub-wide
rather than per camera — measured on this hardware, two of six ids appeared on
both doorbells — so the same number really does follow one person from door to
door. Its `trail` attribute lists recent sightings newest first, each with a
camera and a time, capped at 20.

### Arriving or leaving

**Configure → Camera layout** gives each camera a number for how close it is to
your door — 0 for the one nearest the street, higher as they approach. With
that set, the location sensor's `direction` attribute reads `approaching` or
`leaving`.

The form arrives with the numbers already filled in wherever the recordings can
supply them. The hub reports no geometry — the order cameras appear in the
paired list is just the order they were added — but people arrive from the
street and walk towards the door, so whichever camera saw somebody **first** is
the one nearer the street. Every hop between two cameras within three minutes
counts once, and the order falls out of the totals.

It stays a suggestion: it fills in the boxes and nothing is stored until you
submit, and anything you have already saved is left alone rather than
overwritten on each visit. Nothing is suggested at all until somebody has
actually been seen crossing between two cameras — a guessed direction is worse
than none.

Leave every camera on the same number to turn it off.

No direction is reported unless it is genuinely known — one sighting, unranked
cameras, two sightings more than three minutes apart, or a move between
cameras at the same distance all produce nothing rather than a guess.
"Approaching the door" is the kind of signal people wire a siren to.

It reports where the hub last **saw** someone, which is not where they are.
Nobody is tracked between sightings, and a quiet camera means nothing was
detected rather than that the person left — the state simply stops changing.
Outside the poll window there is no value at all, rather than a stale one. Unnamed faces are
still counted and still appear on the cards — naming decides who is worth an
entity, not who gets tracked.

### Backing the names up

Face names and the camera layout are the only state here a hub cannot
reproduce. Recordings and settings live on the hub and every sensor is derived;
these two came out of your head after opening photographs to work out who a
twelve-digit number is — and they live on the config entry, so deleting the
integration takes them with it without warning.

```yaml
action: tapo_h500.backup_names
data:
  config_entry_id: <hub>
```

The response pastes straight back:

```yaml
action: tapo_h500.restore_names
data:
  config_entry_id: <hub>
  face_names: {"272465657857": "Alice"}
  camera_order: {"Front Doorbell": 1, "Side Gate": 0}
  replace: false      # the default: merge rather than discard
```

Merging is the default because the usual restore is an older backup onto an
entry that has learned a few more names since. A blank name removes rather than
stores it, the same rule the card and the options screen use. Leave
`camera_order` out and the layout is untouched — a backup taken before the
layout existed must not read as "the layout is empty".

### Calendar

Each camera gets a `calendar.<camera>_recordings` entity, so the Calendar panel
answers "what happened last Tuesday" — scroll to Tuesday. Each entry says what
the hub detected, names anyone recognised, and carries the face ids of anyone
not yet named.

Entries come from the hub, not from the polled day. A calendar built on the
24-hour window would show one day and nothing before it, which reads as a quiet
fortnight rather than an absent one. Scrolling a long way back is capped at 31
days per view, because the hub's detection search returns at most 1000 records
and a year view would silently show a fraction of the year.

The entity state is always off. A doorbell has no future, and a recording is
indexed only once it has finished, so nothing is ever "in progress"; its
attributes carry the most recent one.

### Prowling

`binary_sensor.<hub>_prowling` is on when somebody has gone round the house
rather than up to the door — they came back to a camera they had already been
past, with somewhere else in between, inside ten minutes. Its `faces`
attribute says who, by name where they have one.

Unlike direction, this needs no camera layout: it does not matter which camera
is nearer the street, only that the same place was reached twice. Two cameras
are enough — front, side, front is a circuit.

Consecutive sightings at one camera collapse first, so standing at the front
door long enough for three clips is not a lap (that is the loitering sensor),
and two visits hours apart are two visits.

### Snooze

`switch.<hub>_notifications_snoozed` mutes notifications without disabling the
automation — the thing people forget to turn back on. Flip it by hand for an
indefinite snooze, or give it a duration:

```yaml
action: tapo_h500.snooze
data:
  config_entry_id: <hub>
  minutes: 60        # omit for indefinite, 0 to cancel
```

Nothing stops recording, downloading or firing events. Footage taken during a
snooze is the footage most likely to be wanted afterwards. Only an automation
that reads the switch is affected, and the notification blueprint has a
**Snooze switch** input for exactly that — left empty, nothing changes.

It does not survive a Home Assistant restart, deliberately: a snooze that
outlived one would be a silent doorbell nobody remembered turning off.

### Unusual activity, per camera

`binary_sensor.<camera>_unusual_activity` compares the last hour against the
camera's own recent rate, not a fixed number — a doorbell on a main road and a
back gate do not agree on what busy means.

**Configure → Unusual activity** sets how far above that average each camera
has to get. *Sensitive* is twice its typical hour, *Normal* three times (what
has always been used, and still the default), *Relaxed* five times and needs
more activity before it considers the question at all.

Per camera because the same numbers cannot fit two: three times typical is a
Saturday afternoon on a doorbell facing a pavement, and somebody in the garden
on a back gate. The `multiplier` and `minimum_per_hour` attributes say what the
sensor is measuring against, so "why has this not fired" is answerable without
reading the source.

### Storage forecast

`sensor.<hub>_storage_full_in` is how many days until the hub starts
overwriting, at the rate it has been filling. Full is not a failure — loop
recording silently discards the oldest footage rather than stopping — so this
is the deadline for downloading anything worth keeping.

The hub reports how full it is and nothing about how full it was, so the rate
is sampled while Home Assistant runs, once a minute from the status refresh
that already happens. That means:

- **Unavailable for the first hour** after a restart. The figure is rounded to
  a tenth of a percent, so a shorter run measures the rounding.
- **Unavailable when the disk is not filling** — a hub already overwriting sits
  at a steady figure forever, and a line fitted to that noise would say
  something like "full in 4000 days".
- The history restarts if the figure drops, which is what a format, a swapped
  card or loop recording catching up all look like.

`percent_per_hour` and `samples` attributes say which of those applies.

### What was different about today

`tapo_h500.daily_summary` returns `summary` — a count per camera — and
`highlights`, which is the part worth reading. Counts are the honest thing to
report and not what anybody opens a digest for: "Front: 48 recordings (12
person, 3 vehicle)" is the same sentence every day, so a day worth knowing
about looks exactly like a day that was not.

`highlights` is usually **empty**, and that is the whole design. It only ever
says:

- a camera reported being tampered with — always first, however far down its
  camera's name would put it;
- a camera had a genuine peak — measured against its own flat-day average, so a
  doorbell on a pavement seeing five an hour all day has no peak while a back
  gate seeing five in one hour does;
- unfamiliar faces after dark, using the configured night window — the same
  face at three in the afternoon is a delivery;
- somebody who stayed at a camera longer than three minutes;
- a camera that recorded nothing at all.

Everything comes from the same 24-hour window as the rest of the integration.
There is no comparison against last week, because there is no last week here.

Assist's "what happened today" leads with these lines and then reads the
counts, so a dull day sounds dull.

### Picking a trigger without writing a template

**Settings → Automations → Create → Device** lists everything this integration
works out, in three groups.

Pick a **camera** and you get one trigger per detection the hub can report —
*detected a person*, *was rung*, *saw an unfamiliar face*, *reported tampering*
— plus *someone the hub does not recognise has waited there* and *a visit that
looked like a delivery*. Detections match against the full `events_1` mask, so
a person who also tripped plain motion fires the person trigger as well as the
motion one, which is what anyone building an automation expects.

Pick the **hub** and you get the three that are about the house rather than one
camera: *someone went round the house*, *someone you have named arrives home*,
and *a visit begins*. The last two are bus events, so they carry data no entity
state can — who, which cameras, what the hub saw — and they fire once per
person rather than once per recording. Both are filtered to that hub, so a
two-hub installation does not announce the other house's front door.

The state-backed triggers fire only on turning **on**. They all clear
themselves, and firing again as somebody walks away is how an automation gets
muted.

### A camera being handled

Detection code 19 is the hub's own tamper alarm, confirmed by lifting the front
camera off its bracket at 11:16:16 on 2026-08-13. It is the one detection here
that is not about something outside the house — it is about the camera itself,
and if it is real then the recordings after it are the ones that will be
missing.

`binary_sensor.<camera>_theft` reports it for 30 seconds, which is right for a
history graph and useless for a fact somebody needs whenever they next open
Home Assistant. So it also raises a **repair issue** naming the camera, the
local time, and how many reports there have been in the last day — once is a
knock, repeatedly is not. Nothing can dismiss it; it clears when the report
ages out of the 24-hour window.

Matched against the full `events_1` mask rather than `alarm_type`. `alarm_type`
reports only the most significant code, and 20 outranks 19 — so a camera lifted
off its mount while somebody the hub recognised stood there would otherwise
report as a face and nothing else.

### A camera that has gone quiet

`binary_sensor.<camera>_silent` is on when the camera's own hourly history
says about three events should have happened during the current silence, or
when the silence passes the configured ceiling (default 24 hours) — whichever
comes first. A doorbell doing 25 clips a day reads as dead within hours; the
back gate doing 2 stays patient; a normal night accrues nothing. The
`expected_events` attribute shows how far along that count is, and a repair
issue is raised as well.

A camera off the Wi-Fi, flat or unplugged is otherwise invisible — every entity
keeps showing its last value, and the usual way to find out is needing the
footage. The hub offers nothing to check instead: its paired-device record has
16 fields and not one is an online flag, a signal strength or a battery.

So silence is the only evidence, and the entity is named for what it knows.
A back gate that genuinely sees nobody all day will trip it; raise the
threshold under **Configure → Settings**. It cannot go above 24 hours,
because that is how far back the hub is asked.

Unknown, not off, before the first check completes.

### Today, in one picture

`image.<camera>_today` is a contact sheet: every frame from today's recordings,
four across, newest twenty-four, oldest first. A doorbell produces dozens of
near-identical fifteen-second clips a day, and looking through them means
opening dozens of things.

Built with ffmpeg, which already makes every thumbnail here — no image library
was added for it. It rebuilds when a clip downloads, not when the hub reports
an event, because the frame it needs is written by the download.

Unavailable on a quiet day rather than showing a blank sheet.

### Possible delivery

`binary_sensor.<camera>_possible_delivery` comes on for five minutes after a
visit that looked like a delivery: somebody was there, the hub did not
recognise them, they stayed under a minute, and it was daylight.

**Retrospective, and it has to be.** At the moment the hub reports a detection
the person has been there for one clip — and so has everybody who is about to
stay for ten minutes. A visit's length is only known once it is over, so this
cannot answer "is that a delivery at my door right now". It answers "was that a
delivery", and holds the answer long enough for an automation to see it.

A guess, and named like one. Nothing the hub reports says *courier*. A
canvasser looks identical, and so does somebody checking whether the house is
empty — which is why it is a signal to describe an afternoon with, not a reason
to stay quiet.

### Loitering

`binary_sensor.<camera>_loitering` is on while a face the hub could **not**
recognise has been at that camera for more than three minutes. Its `seconds`
attribute is how long.

The hub reports moments, never presence — four minutes at the door arrives as a
string of short clips, and every other signal here counts those as separate
events. Recordings less than two minutes apart are treated as one visit, and
the duration is measured from the first sighting to the last, not to now: a
single fifteen-second clip is evidence of fifteen seconds.

A recognised face never triggers it, however long they wait. Nor does motion
alone, and nor does a visit that has already ended.

### Arriving home, once

The detection event fires every time anyone crosses a camera. For a household
that is the wrong grain — someone working from home trips the front camera a
dozen times a day, and only the first is news.

`tapo_h500_arrival` fires once per **named** person per local day, on their
first sighting:

```yaml
triggers:
  - trigger: event
    event_type: tapo_h500_arrival
actions:
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.event.data.name }} is home ({{ trigger.event.data.camera }})"
```

`name`, `face_id`, `camera`, `at`, and `direction` where the cameras have been
ranked. Unnamed ids never fire it — a stranger appearing is what the ordinary
detection event already reports, and "Face 481036337152 has arrived" helps
nobody.

Silent on the first check after a restart. The window holds a day of
recordings, so otherwise restarting at teatime would announce everyone who came
home at breakfast.

### One notification per visitor

The arrival event covers people you have named. `tapo_h500_visit` covers
everybody, and answers the other half of the same problem: the hub reports
moments rather than presence, so four minutes at the door arrives as a string
of fifteen-second clips, and an automation wired to the detection event sends
sixteen notifications about one visitor.

```yaml
triggers:
  - trigger: event
    event_type: tapo_h500_visit
actions:
  - action: notify.mobile_app_phone
    data:
      message: >-
        {{ trigger.event.data.detection }} at the
        {{ trigger.event.data.camera }}
```

Recordings less than two minutes apart are one visit — the same grouping the
loitering sensor uses. The payload carries `camera`, `camera_index`, `at`,
`cameras`, `detections` (the alarm codes), `detection` (the same phrase the
cards show), `face_ids`, `names` for anyone you have named, `night` — whether
it fell inside the configured night window, decided by the integration so
nothing downstream re-implements a window that wraps midnight — and
`recordings`.

The **Tapo H500 announce a visit** blueprint is built on it: one message per
visitor, with an optional strangers-only filter, an after-dark gate, and the
snooze switch.

**Two cameras watching one path still fire once.** Visits at different cameras
within 30 seconds are one arrival, and so are visits up to three minutes apart
that carry the same face id — recognised at the gate and again at the door is
one person walking, where two strangers two minutes apart are not. The event is
keyed on where they were seen **first**, because that is where they came from,
and `cameras` lists everywhere that saw them. One camera's own recordings are
never merged this way; they are already grouped into visits, and merging them
again would swallow a real second visitor at the same door.

It fires at the **start** of the visit, so it only knows about the first
recording. That is deliberate: at that moment somebody who is about to leave in
ten seconds and somebody who is about to stay ten minutes look identical, which
is why `binary_sensor.<camera>_possible_delivery` and
`binary_sensor.<camera>_loitering` exist separately and are both retrospective.

Silent until the first poll has completed, for the same reason arrivals are.

## More than one hub

Add each hub separately; they are keyed by address, so two work. Actions all
take a `config_entry_id`, the cards have a **Hub** picker (leave it empty and
they use the first one, so existing cards keep working), and the spoken
"what happened today" covers every hub.

One thing to watch: downloads are filed under a slug of the camera's own name,
which is what makes "already downloaded" a check of the files on disk rather
than a separate index. Two cameras called the same thing — likely across two
hubs — share a folder, and one camera's recording then answers that question
for the other. The integration raises a repair issue naming them; rename one in
the Tapo app and new recordings go to the new folder.

Aliases that differ only in case or spacing count as the same name, because
they slug to the same directory.

## Options


**Settings → Devices & services → Tapo H500 → Configure**

| Option | Default | Effect |
| --- | --- | --- |
| Seconds between activity checks | `2` | The whole notification delay — nothing arrives sooner than the next check. A check costs the hub about 40ms, so `1` is allowed. |
| Download new recordings automatically | Every new recording | `Never`, `Doorbell presses only`, or `Every new recording`. |
| Only download these detections | *nothing ticked* | Empty means no filter, which is what happens today. Tick some and a recording has to carry one of them to be downloaded — person and doorbell are the clips people go back for; vehicles on a camera facing a road are the traffic. It narrows the setting above rather than overriding it, so `Doorbell presses only` plus `Person` downloads presses that also had a person in them. Changing it does not reload the integration, so it costs no hub login. |
| Downloaded clips to keep per camera | `0` | `0` keeps everything. Any other number prunes the oldest automatic downloads. Manual downloads are never pruned. |
| Doorbell presses to keep per camera | `0` | `0` treats them like everything else. Any other number keeps that many of the newest presses whatever their age. |
| Recordings with a person to keep per camera | `0` | The same, for recordings with a person in them. Counted separately from presses. |
| Hours of silence before a camera is flagged | `24` | The ceiling. The silent sensor usually flags much earlier, from the camera's own history; this is the longest it can possibly take, and a camera with no history at all still trips it here. Cannot go higher — the hub is only asked about a day. |
| Convert downloads to MP4 | On | Off keeps the hub's original MPEG-TS. |
| Days the cards show by default | `1` | Cards whose own "Days to show" was never set follow this, so changing every dashboard from a day to a week is one field. A card with its own days keeps it. Changing it costs no hub login. |

## Media layout

```
<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.mp4
<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.jpg
<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.json
```

The `.json` is a small sidecar recording what triggered the clip, written at
download time because the hub's own index only reaches back a day. It powers
the type folders below and is deleted whenever the clip is.

Under **Media → Tapo H500** each camera and each date shows the newest frame
beneath it as its cover, rather than a blank tile, so a month of days is
something to look through. Clips show their own frame. Alongside the cameras
sit **Doorbell presses**, **People**, **Vehicles** and **Pets** — the whole
archive filtered by what is in it, newest first, spanning cameras and days.
Clips downloaded before sidecars existed appear only under their camera and
date.

Names come from the clip's start time in Home Assistant's local timezone, so
"is this already downloaded" is a path check rather than a stored index.

## Security notes

- Passwords are kept in the Home Assistant config entry.
- Device IDs and MAC addresses are not returned by service actions.
- Clip and thumbnail URLs handed to the dashboard are signed and expire after
  12 hours; the media directory stays behind Home Assistant auth.
- Do not expose TCP port `8800` to the internet.
