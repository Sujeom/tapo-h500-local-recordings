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
| `camera.<name>` | The frame from that camera's newest downloaded clip. |
| `event.<name>_activity` | Fires `ring` or `motion`, with `start_time`, `end_time`, `duration` and the hub's raw `hub_type` label as attributes. |
| `sensor.<name>_last_activity` | Timestamp of the newest recording. Drives "nothing seen since" automations. |
| `sensor.<name>_recordings_24h` | How many clips the hub holds for this camera. |
| `sensor.<name>_ai_enhance`, `_network_mode`, `_model` | Diagnostics. |
| `binary_sensor.<name>_hub_storage`, `_24_7_recording`, `_ai_enhance_enabled`, `_wifi_backup` | Diagnostics. |

Everything above comes from one extra `multipleRequest`, batched into a
single round trip because this hub is easy to overload.

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
that camera's dialog, and a Recordings button opens the media browser. Plain motion never notifies on its own but is still described when it
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

Always copy the exact time boundaries from `list_recordings`. `convert_to_mp4`
optionally overrides the integration option for a single download.

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
