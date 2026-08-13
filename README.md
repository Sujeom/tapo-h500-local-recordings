# Tapo H500 Local Recordings for Home Assistant

Experimental HACS custom integration for browsing, downloading and automating
recordings stored on a Tapo H500 HomeBase.

> **Work in progress.** This is unfinished and changing. It talks to an
> undocumented protocol that has been reverse engineered against exactly one
> setup — a single H500 running firmware `1.3.20` with two paired TD21
> doorbells. Entity names, the on-disk media layout, action fields and option
> defaults may all change without a migration path, and a firmware update may
> break it outright. Some things listed below are known not to work; see
> **Scope and limitations** and `docs/protocol-notes.md`, which records what has
> actually been observed versus what is still guesswork.

## What works

- Connects directly to the H500 on your LAN.
- Lists paired hub-managed cameras/doorbells.
- Lists indexed H500 recordings for a date range.
- Polls the hub and raises a Home Assistant **event entity** per camera when a
  doorbell press or motion clip appears.
- Downloads new recordings automatically.
- Converts downloads to MP4 so they play in the browser.
- Generates a JPEG thumbnail for every downloaded clip.
- Serves a **camera entity** per doorbell showing the newest event frame.
- Browses downloaded clips under **Media → Tapo H500**, by camera and date,
  with thumbnails.
- Ships a **dashboard card** listing the hub's clips with play, download and
  delete buttons.
- Deletes downloaded copies; can format hub storage (see the warning below).
- Does **not** call `preWakeUp`, `preVod`, or a TP-Link cloud media endpoint.

On firmware 1.3.20 the hub reports media encryption as on but sends an empty
`nonce` in its `Key-Exchange` header. `pytapo` rejects any empty nonce, which
fails every download; the integration allows it through, because the hub
derives its key from the empty value and the resulting stream decrypts
correctly. Verified end to end: `ffprobe` reports MPEG-TS with H.264 at
2304x1296.

The TP-Link cloud-account password is still required by Tapo's **local**
port-8800 media encryption handshake. It is stored in the Home Assistant config
entry and is not placed in filenames, service responses, or logs.

## Scope and limitations

**No live stream.** The camera entities serve stills taken from the newest
recording, not live video. The H500's live media session for hub-attached
battery cameras is not part of the path verified against real hardware, so it
is deliberately not attempted here.

`tools/probe_live.py` exists to find that missing verb by asking the hub rather
than sniffing the Tapo app. Phase A is free and may already answer it:

```
python3 -m venv .venv && .venv/bin/pip install pytapo==3.4.18
.venv/bin/python tools/probe_live.py --host 192.168.1.50 --camera 1
.venv/bin/python tools/probe_live.py --host 192.168.1.50 --camera 1 --probe
```

It needs `pytapo` for transport and crypto, and a machine that can reach the
H500 on the LAN. Only `--self-test` runs without it.

`--probe` opens real media sessions, which wakes a battery doorbell, so it runs
one verb at a time and stops at the first that returns video. Passwords come
from `TAPO_PASSWORD`/`TAPO_CLOUD_PASSWORD` or a prompt, never the command line.
Read the error codes: a "method does not exist" code means the verb is wrong, a
parameter complaint means the verb is right and only the fields need fitting.

**No per-clip deletion on the hub.** The hub exposes no delete-one-recording
call — `pytapo` has none, and TP-Link's own documentation says SD/hub footage
can only be removed by formatting. `tapo_h500.delete_recording` therefore
removes the *downloaded copy* in Home Assistant. `tapo_h500.format_hub_storage`
is the only hub-side deletion that exists and it **erases every recording for
every paired camera, with no undo**.

**Event latency.** The integration prefers the hub's detection log, but on
firmware 1.3.20 that log is accepted and always empty, so in practice it falls
back to polling the indexed clip list after one attempt. A clip appears only
once the hub has finished writing it, so an event trails the actual doorbell
press by the poll interval plus the clip length.

**No AI classification.** The hub exposes no face, person, pet or vehicle
detection to a local client, and every clip is labelled `video_type` `"2"`
whatever triggered it. The cameras do the classification — `ai_camera_support`
is a non-zero bitmask — but the result is not retrievable. See
`docs/protocol-notes.md`.

The protocol is undocumented. Pinning the H500 to a stable LAN address is
strongly recommended. Firmware changes may require integration updates.

Requires Home Assistant 2024.11 or newer.

## HACS installation

HACS installs integrations from a GitHub repository. After this repository is
uploaded to GitHub:

1. In HACS, open **Integrations**.
2. Open the three-dot menu and choose **Custom repositories**.
3. Enter the GitHub repository URL and choose category **Integration**.
4. Install **Tapo H500 Local Recordings**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration**.
7. Search for **Tapo H500 Local Recordings**.

### Installing from a self-hosted GitLab or Gitea

HACS accepts **only public GitHub repositories**, so a self-hosted origin cannot
be added as a custom repository. Install from a clone instead — clone this
repository on the machine running Home Assistant and run:

```
./tools/install-to-ha.sh /config
```

Pass whichever directory holds `configuration.yaml`. Updating is the same
command after a pull:

```
git pull && ./tools/install-to-ha.sh /config
```

Restart Home Assistant afterwards. The script replaces the component wholesale
rather than merging, so files removed upstream do not linger.

For a one-off manual installation, copy `custom_components/tapo_h500` into Home
Assistant's `custom_components` directory and restart.

## Configuration

The config flow asks for:

- **H500 IP address**
- **Camera account username** (normally `admin`)
- **Camera account password**
- **TP-Link cloud account password** (used only to derive local media encryption keys)

The H500 and Home Assistant must be able to reach each other over the LAN. The
integration uses HTTPS/control traffic to the hub and TCP port `8800` for
recording downloads.

### Options

**Settings → Devices & services → Tapo H500 → Configure**

| Option | Default | Effect |
| --- | --- | --- |
| Seconds between activity checks | `20` | How often the hub is polled. |
| Download new recordings automatically | Every new recording | `Never`, `Doorbell presses only`, or `Every new recording`. See the note below before choosing presses-only. |
| Downloaded clips to keep per camera | `0` | `0` keeps everything. Any other number prunes the oldest automatic downloads once a camera holds more than that, so set `5` and the sixth arrival evicts the oldest. Manual downloads are never pruned. |
| Convert downloads to MP4 | On | Off keeps the hub's original MPEG-TS. |

## Entities

The hub gets its own device, and each paired camera gets one.

**Hub**

| Entity | Use |
| --- | --- |
| `sensor.*_storage_free` / `_storage_total` / `_storage_used` | Recording space, in GB and percent. Automate a warning before the loop starts overwriting. |
| `sensor.*_storage_status` | The hub's own word for the disk state, e.g. `normal`. |
| `binary_sensor.*_storage_problem` | On when the disk is not `normal`. |
| `binary_sensor.*_siren` + `sensor.*_siren_time_left` | Whether the hub siren is sounding, and for how much longer. |
| `sensor.*_firmware_state`, `sensor.*_ip_address` | Diagnostics. |
| `binary_sensor.*_loop_recording`, `_led`, `_media_encryption` | Diagnostics. |

**Per camera**

| Entity | Use |
| --- | --- |
| `camera.<name>` | The frame from that camera's newest downloaded clip. |
| `event.<name>_activity` | Fires `ring` or `motion`, with `start_time`, `end_time`, `duration` and the hub's raw `hub_type` label as attributes. |
| `sensor.<name>_last_activity` | Timestamp of the newest recording. Drives "nothing seen since" automations. |
| `sensor.<name>_recordings_24h` | How many clips the hub holds for this camera. |
| `sensor.<name>_ai_enhance`, `_network_mode`, `_model` | Diagnostics. |
| `binary_sensor.<name>_hub_storage`, `_24_7_recording`, `_ai_enhance_enabled`, `_wifi_backup` | Diagnostics. |

Everything above comes from one extra `multipleRequest` per poll, batched into a
single round trip because this hub is easy to overload.

**No battery level.** None of the battery getters work on an H500 — the reading
lives on the camera, and the hub exposes no way to address a camera child. See
`docs/protocol-notes.md`.

Cameras are enumerated when the config entry loads. Pair a new camera, then
reload the integration to pick it up.

**Doorbell presses are not distinguishable yet.** An H500 with TD21 doorbells
labels every clip `video_type` `"2"`, so nothing classifies as a `ring` and the
presses-only download mode would match nothing. The raw label is exposed as the
`hub_type` event attribute; if you see a press arrive with a different code,
that code is the missing piece.

### Doorbell automation

```yaml
automation:
  - alias: Someone at the door
    triggers:
      - trigger: state
        entity_id: event.side_doorbell_activity
        attribute: event_type
        to: ring
    actions:
      - action: notify.mobile_app_phone
        data:
          message: Someone rang the doorbell
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

Add a manual card:

```yaml
type: custom:tapo-h500-card
days: 2
max_height: 400
```

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

## Media layout

```
<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.mp4
<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.jpg
```

Folder and file names come from the clip's start time in Home Assistant's local
timezone, so whether a clip is already downloaded is a path check rather than a
stored index. Earlier versions wrote flat `<Camera>_<unixtime>.ts` files; those
are not migrated and can be deleted or moved by hand.

## Security notes

- Passwords are kept in the Home Assistant config entry.
- Device IDs and MAC addresses are not returned by service actions.
- Clip and thumbnail URLs handed to the dashboard are signed and expire after
  12 hours; the media directory itself stays behind Home Assistant auth.
- Do not expose TCP port `8800` to the internet.

## Verification performed

Verified against a physical H500 with paired TD21 doorbells:

- The recording download path, using stock `pytapo==3.4.18` for transport and
  crypto with this integration supplying the app-derived H500 request framing.
- A bounded TD21 recording download reached the explicit finished notification
  and returned 3,398,852 bytes; `ffprobe` identified MPEG-TS, H.264 video and a
  15.07-second duration.
- The empty-nonce workaround on firmware `1.3.20`, which stock `pytapo` rejects:
  205,108 bytes retrieved and decrypted, `ffprobe` reporting MPEG-TS with H.264
  at 2304x1296.
- The hub's module inventory, and that `preWakeUp` is a component with no
  corresponding method. See `docs/protocol-notes.md`.

Verified by unit test (`python3 -m unittest discover -s tests`, 14 tests, no
hub or Home Assistant install required):

- The H500 download request payload and the required `Content-Length: 0` outer
  framing.
- The verified 25-packet acknowledgement window and finished-notification
  handling.
- Doorbell-versus-motion classification, both timestamp field spellings, clip
  flattening, and camera-name sanitising against path traversal.
- That an unsupported detection search disables itself after one rejection
  rather than being retried every poll.
- That an empty nonce reaches key derivation intact, a real nonce is passed
  through untouched, and the session module resolves the patched helper.

**Not yet verified against hardware:** hub storage formatting, and everything
that runs inside Home Assistant — the event entities, automatic download,
thumbnail generation, MP4 conversion, the media browser and the dashboard card.
Those are written against the verified protocol calls but have not been
exercised in a running Home Assistant instance.

**Known not to work:** doorbell presses cannot be told apart from motion on this
firmware, and there is no live video.

## License

MIT. The integration depends on the separately distributed MIT-licensed
`pytapo` package.
