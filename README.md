<img src="https://raw.githubusercontent.com/Sujeom/tapo-h500-local-recordings/main/brand/logo.png" alt="Tapo H500 Local Recordings" width="380">

Home Assistant integration for a Tapo H500 HomeBase. Browse, download and
automate the recordings the hub already stores, entirely over your LAN.

*Not affiliated with or endorsed by TP-Link. The artwork in `brand/` is original
to this project; "Tapo" and "H500" name the hardware it talks to.*

> **Work in progress.** The H500 protocol is undocumented and this was reverse
> engineered against one setup — firmware `1.3.20` with two paired TD21
> doorbells. Entity names, media paths and options may change without a
> migration, and a firmware update may break it. See
> [docs/limitations.md](docs/limitations.md).

## What you get

- A **camera entity** per doorbell showing the newest event frame.
- An **event entity** per camera that fires on a doorbell press, a person, an
  animal and more — within about a second of the hub seeing it.
- Recordings downloaded automatically, converted to MP4, with thumbnails.
- Thumbnails for clips that have **not** been downloaded yet.
- **Six dashboard cards**: list, newest event, thumbnail grid, timeline, face
  summary and an events-per-hour chart.
- Clips under **Media → Tapo H500**, by camera and date.
- Hub controls: siren, LED, storage figures and more.

## Requirements

- A Tapo H500 HomeBase reachable from Home Assistant over the LAN.
- The camera account password, and your TP-Link cloud password — the latter is
  used **only** to derive the local media-encryption key, never sent anywhere.
- Nothing to install by hand. `ffmpeg` ships with Home Assistant.

## Installation

### HACS

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Enter this repository's URL, category **Integration**.
3. Install **Tapo H500 Local Recordings**, then restart Home Assistant.
4. **Settings → Devices & services → Add integration**, search for
   **Tapo H500**.

### Manually, or from a self-hosted GitLab or Gitea

HACS accepts only public GitHub repositories, so install from a clone instead.
On the machine running Home Assistant:

```
./tools/install-to-ha.sh /config
```

Pass whichever directory holds `configuration.yaml`. Updating is the same
command after a `git pull`. Restart Home Assistant afterwards.

For a one-off, copy `custom_components/tapo_h500` into Home Assistant's
`custom_components` directory and restart.

## Configuration

The setup form asks for:

| Field | Notes |
| --- | --- |
| **H500 IP address** | Must be reachable from Home Assistant. |
| **Camera account username** | Use `admin`, **not** your TP-Link email — see below. |
| **Camera account password** | |
| **TP-Link cloud password** | Only used to derive local media-encryption keys. |
| **Seconds between activity checks** | Defaults to `2`. Also changeable later. |

> **Use `admin`, not your TP-Link email.** The hub refuses the cloud email with
> an error indistinguishable from a lockout, so a wrong username here looks
> exactly like a hub that has stopped responding.

The integration uses HTTPS to the hub, and TCP port `8800` for downloads.

### Options

**Settings → Devices & services → Tapo H500 → Configure**

| Option | Default | Effect |
| --- | --- | --- |
| Seconds between activity checks | `2` | The whole notification delay — nothing arrives sooner than the next check. A check costs the hub about 40ms, so `1` is allowed. |
| Download new recordings automatically | Every new recording | `Never`, `Doorbell presses only`, or `Every new recording`. |
| Downloaded clips to keep per camera | `0` | `0` keeps everything. Any other number prunes the oldest automatic downloads. Manual downloads are never pruned. |
| Convert downloads to MP4 | On | Off keeps the hub's original MPEG-TS. |

## Getting notified

A ready-made automation is in
[`examples/notify-person-pet-doorbell.yaml`](examples/notify-person-pet-doorbell.yaml):
one automation covering every camera, firing only for a person, an animal or the
doorbell, and saying which happened where —

> **Someone rang the Front Doorbell**
> Saw a doorbell press, a person, an animal and motion, 5:41 PM

It attaches the camera's frame, opens that camera when tapped, and offers a
Recordings button. Change `notify.mobile_app_phone` to your notify service;
nothing else needs editing.

## Dashboard cards

Add a card and search for **Tapo**. All six have a visual editor, are
resizable, and can be pinned to a single camera.

| Card | Shows |
| --- | --- |
| List | Recordings with play, download and delete |
| Hero | The newest event, large |
| Grid | Thumbnail grid |
| Timeline | Events by hour |
| Faces | Detected faces, groupable and nameable |
| Summary | Events per hour, as a bar chart |

## Media layout

```
<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.mp4
<media>/tapo_h500/<camera>/<YYYY-MM-DD>/<HHMMSS>.jpg
```

Names come from the clip's start time in Home Assistant's local timezone, so
"is this already downloaded" is a path check rather than a stored index.

## Security notes

- Passwords are kept in the Home Assistant config entry.
- Device IDs and MAC addresses are not returned by service actions.
- Clip and thumbnail URLs handed to the dashboard are signed and expire after
  12 hours; the media directory stays behind Home Assistant auth.
- Do not expose TCP port `8800` to the internet.

## Documentation

| | |
| --- | --- |
| [docs/reference.md](docs/reference.md) | Every entity, card option and action |
| [docs/limitations.md](docs/limitations.md) | What does not work, and what has been verified |
| [docs/protocol-notes.md](docs/protocol-notes.md) | The reverse-engineering evidence |

## License

MIT. The integration depends on the separately distributed MIT-licensed
`pytapo` package.
