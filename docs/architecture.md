# How the pieces fit

Thirty-three modules. This says which does what, and — more usefully — which
ones you have to think about together.

Every one-line summary below is that module's own first docstring line, and a
test fails if a module is missing here or described here and gone. A map that
drifts is worse than no map.

## The shape of it

One config entry is one hub. Setting it up creates a client, a coordinator and
one device per paired camera; everything else hangs off the coordinator.

```
     the hub on the LAN
            │  443 (control)          8800 (media)
            ▼                              ▼
        api.py ──────────────────────► media.py
            │  raw JSON                    │  clips and thumbnails on disk
            ▼                              │
     status.py, clips.py                   │
            │  parsed readings             │
            ▼                              ▼
       coordinator.py ◄──────────── media_health.py
            │
            │  one poll every 2s, published to every entity
            ▼
   the platforms: sensor, binary_sensor, camera, image, event, switch,
   number, select, siren, button, update, calendar
            │
            ▼
   repairs.py, diagnostics.py, logbook.py, device_trigger.py, intent.py
```

Two things do not go through the coordinator: the dashboard cards, which call
`services.py` over the websocket, and the media browser, which reads the
filesystem through `media_source.py`.

## The hub, and what it says

| Module | Lines | What it is |
| --- | --- | --- |
| `api.py` | 742 | Minimal local Tapo H500 recording client. |
| `status.py` | 370 | Reading the hub's status responses. |
| `clips.py` | 1014 | Interpreting what the hub says about clips and detections. |

`api.py` is the only module that talks to the hub. It holds one login for the
life of the entry, because this hub stops responding under repeated
authentication and recovers only on a timeout. `status.py` and `clips.py` are
pure: no Home Assistant, no network, no disk. That is what lets the awkward
parts — bitmask decoding, local-day arithmetic, detection matching — be tested
as the arithmetic they are.

`clips.py` owns every field name the hub uses. Nothing else reads one.

## Polling and state

| Module | Lines | What it is |
| --- | --- | --- |
| `coordinator.py` | 1393 | Polls the hub, turns new activity into events, and downloads rings. |
| `media_health.py` | 206 | Whether the hub is serving recordings, and the record of when it was not. |
| `entity.py` | 69 | Shared device identity, and adding entities for a camera the hub reports after setup. |
| `__init__.py` | 212 | Tapo H500 local recording integration. |
| `const.py` | 655 | Constants for Tapo H500. |
| `models.py` | 55 | The shapes the hub sends, named. |

The coordinator is the big one and it stays big: polling, events, downloads,
faces, visits, arrivals and retention all read the same poll and each other.
`media_health.py` came out of it because that state answers to itself and to
nothing else. `const.py` is long because most of it is the reasoning behind a
number rather than the number.


## What a poll actually asks the hub

Everything on this page happens on the LAN. The integration issues no request
to TP-Link, ever — not an update check, not telemetry, nothing. See
[README](../README.md#local-only-by-design).

**Every poll**, per camera, one batched round trip that carries both the clip
index and the detection log for the last day (`LOOKBACK_SECONDS`, 86400). They
were two calls until the hub was measured; batching them halved the per-poll
load on a device that overloads easily.

**Every 60 seconds of wall clock** (`STATUS_MAX_AGE`), one further round trip
for hub status — the LED, the siren, storage, firmware, the clock. It is
expressed as a number of polls rather than a timer, so a longer interval does
not silently make status stale: at the default two seconds it is every 30th
poll. A write forces the next poll to read it, or a control would snap back to
its old value for up to a minute.

**Every 300 seconds** (`CAMERAS_MAX_AGE`), the paired camera list. It changes
only when somebody pairs or unpairs a doorbell and costs 58ms — more than the
detection lookups it used to precede. It is never left empty: a failure with
nothing cached still fails the poll, because a hub with no cameras is not a
state this integration can do anything useful with.

### The interval, and when it is not the interval

The default is **2 seconds** (`DEFAULT_POLL_INTERVAL`), set at setup and
changeable under Configure, bounded between 1 and 600. It is the whole
notification delay: a doorbell press is not known until the poll that finds
it.

Two things move it:

- **Idle backoff.** After ten minutes with nothing new (`POLL_IDLE_AFTER`),
  the interval relaxes to at least six seconds (`POLL_IDLE_INTERVAL`) and
  snaps back the moment anything happens. A quiet house at three in the
  morning does not need the hub asked every two seconds.
- **Failure backoff.** Consecutive failures widen the interval up to five
  minutes (`POLL_BACKOFF_MAX`). A hub that has wedged recovers on a timeout
  rather than on a retry, so continuing to ask at full speed is the one thing
  that stops it recovering.

The clock starts at "just now" on a restart rather than at zero, so a fresh
process runs at full speed for the first ten minutes instead of treating
itself as a quiet house.

### What it never does

No poll opens a media session. Downloads, previews and frames are separate,
serialised behind their own lock, and started by something asking — an
automatic download, a dashboard tile, a service call. One session at a time,
always: this hub wedges under concurrent ones.

## Files on disk

| Module | Lines | What it is |
| --- | --- | --- |
| `media.py` | 604 | Filesystem and ffmpeg side of the H500 integration. |
| `media_source.py` | 298 | Browse downloaded H500 clips under Media, by camera and date. |
| `preview.py` | 96 | Thumbnails for clips that have not been downloaded, made on demand. |
| `contact_sheet.py` | 128 | The day in one picture. |
| `backup.py` | 119 | Taking the typed-in state out and putting it back. |

Everything is addressed by `<camera>/<date>/<HHMMSS>`, derived from the clip's
start time in local time. That is what makes "already downloaded" a question
about the files rather than an index that could disagree with them — and why
the hour the clock goes back needs a suffix.

## Entities

| Module | Lines | What it is |
| --- | --- | --- |
| `sensor.py` | 829 | Hub and per-camera sensors, built only from responses seen on real hardware. |
| `binary_sensor.py` | 642 | Hub and per-camera on/off state. |
| `image.py` | 178 | One still per camera: the frame from its most recent event. |
| `event.py` | 157 | Doorbell and motion events for each paired camera. |
| `switch.py` | 159 | Hub settings that are simply on or off. |
| `calendar.py` | 141 | Every detection as a calendar entry, so a day can be read at a glance. |
| `siren.py` | 96 | The hub siren, which is a real controllable device on this firmware. |
| `button.py` | 75 | One press to restart the hub. |
| `number.py` | 72 | Siren loudness and run time, as stored on the hub. |
| `update.py` | 63 | Firmware update entity for the hub. |
| `select.py` | 55 | The siren sound, choosable without sounding the siren. |
| `camera.py` | 53 | Still image per paired camera, taken from that camera's newest clip. |
| `hub_control.py` | 39 | Shared plumbing for the writable hub settings. |

Every writable hub setting — the LED, loop recording, the siren — goes through
`hub_control.py`, which writes and then refreshes once. A second poll would be
exactly the traffic this hub struggles with.

## Everything a person interacts with

| Module | Lines | What it is |
| --- | --- | --- |
| `services.py` | 594 | The thirteen services this integration offers. |
| `config_flow.py` | 480 | Config flow for Tapo H500. |
| `repairs.py` | 376 | Conditions worth interrupting someone about, raised as Home Assistant issues. |
| `device_trigger.py` | 202 | Device triggers: everything this integration works out, pickable from the UI. |
| `diagnostics.py` | 178 | Diagnostics download for a hub. |
| `intent.py` | 153 | Assist intents: asking the house what it saw. |
| `logbook.py` | 65 | Logbook entries, so history reads as prose rather than state changes. |

`diagnostics.py` redacts by allow-list: nothing reaches the file unless it was
named. It also describes the shape of what the hub sent, without the values,
because otherwise a field nobody has named yet is invisible and stays that way.

## The dashboard card

`www/tapo-h500-card.js` is one file on purpose. It is served at a URL carrying
the integration's version, so a browser always fetches the new one; a relative
import inside it would drop that query, and sub-modules would cache
independently under fixed URLs. An upgrade could then load a mix of old and
new, which is a silent failure a single file cannot have.

Eight card types share one base class. The base does loading, rendering and
click handling; each card supplies a `body()` and a stylesheet.

## The one thing that reaches into another integration

`_async_register_lovelace_resource` reads `hass.data["lovelace"].resources`
— Lovelace's own storage, with no public API and a layout that has changed
shape across Home Assistant versions. It is the integration's only dependency
on private internals, and therefore the first place to look after a core
upgrade.

It is handled rather than hoped for: the three shapes a changed layout takes
are caught, anything else is allowed to surface, and a failure raises a repair
notice with the manual steps rather than only a log line. Known to work on
2024.11 through 2025.8, checked 2026-08-31.

## What is not here

No database, no state written to disk beyond the recordings and the config
entry. The wedge log, the face cache and the session history all live in memory
and span this Home Assistant's uptime rather than the hub's life. Anything that
needs to outlast a restart is a sensor with a state class, so the recorder
keeps it.
