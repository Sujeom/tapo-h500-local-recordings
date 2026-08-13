<img src="https://raw.githubusercontent.com/Sujeom/tapo-h500-local-recordings/main/brand/logo.png" alt="Tapo H500 Local Recordings" width="380">

Home Assistant integration for a Tapo H500 HomeBase. Browse, download and
automate the hub's recordings, entirely over your LAN.

Gives you a camera and an event entity per doorbell, automatic downloads with
thumbnails, six dashboard cards, and hub controls such as the siren.

> **Work in progress.** Reverse engineered against one setup (firmware `1.3.20`,
> two TD21 doorbells). Entity names and options may change without a migration.
> There is no live view — see [docs/limitations.md](docs/limitations.md).

## Install

**HACS** → three-dot menu → **Custom repositories** → this repo's URL, category
**Integration** → install → restart Home Assistant.

Not on GitHub, or installing by hand? Clone it on the Home Assistant machine and
run `./tools/install-to-ha.sh /config`, passing whichever directory holds
`configuration.yaml`. Restart afterwards.

## Set up

**Settings → Devices & services → Add integration** → **Tapo H500**.

| Field | Notes |
| --- | --- |
| H500 IP address | Must be reachable from Home Assistant |
| Camera account username | Use `admin` — **not** your TP-Link email |
| Camera account password | |
| TP-Link cloud password | Only derives the local media-encryption key |
| Seconds between activity checks | `2`. This is the whole notification delay |

> **`admin`, not your email.** The hub refuses the cloud email with an error
> indistinguishable from a lockout, so a wrong username here looks exactly like
> a hub that has stopped responding.

Downloads use TCP port `8800`. Don't expose it to the internet.

## Then what

- **Notifications:** import the
  [blueprint](blueprints/automation/tapo_h500/notify_on_detection.yaml) —
  **Settings → Automations → Blueprints → Import**, then paste its URL. Pick
  your cameras, your notify service, and which detections matter. It says what
  happened and where, then replaces itself with that event's photograph once
  the hub has finished recording. A plain YAML version is in
  [`examples/`](examples/notify-person-pet-doorbell.yaml) if you would rather
  edit it directly.
- **Faces:** name someone once with the `tapo_h500.name_face` action and every
  card shows it. Each named face also gets a sensor for when they were last
  seen, on any camera.
- **Bug reports:** the integration's three-dot menu offers **Download
  diagnostics** — hub state and detection counts, with no credentials, camera
  names or timestamps in it.
- **Unusual activity:** each camera flags an hour that stands out against its
  own recent rate, so a busy street and a back gate are judged separately.
- **History:** each camera gets a `binary_sensor` per detection — motion,
  person, animal, unfamiliar face, tampering — on for 30 seconds after the
  hub reports it, so activity appears on a history graph and can be used as an
  automation *condition*.
- **Automations:** the doorbells appear under **Device** triggers — *When a
  person is detected*, *was rung*, *saw an unfamiliar face* — so no templates
  are needed.
- **Cards:** add a card, search **Tapo**. All seven have a visual editor.
- **Clips:** **Media → Tapo H500**, by camera and date.
- **Settings:** the integration's **Configure** page.

## Docs

| | |
| --- | --- |
| [reference.md](docs/reference.md) | Every entity, card option and action |
| [limitations.md](docs/limitations.md) | What doesn't work, and what's verified |
| [protocol-notes.md](docs/protocol-notes.md) | The reverse-engineering evidence |

MIT. Depends on the separately distributed MIT-licensed `pytapo`. Not
affiliated with TP-Link; the `brand/` artwork is original to this project.
