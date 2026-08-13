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

- **Notifications:** copy
  [`examples/notify-person-pet-doorbell.yaml`](examples/notify-person-pet-doorbell.yaml)
  and change the notify service. Fires only for a person, an animal or the
  doorbell, says which happened where, then replaces itself with the picture
  once the hub has finished the recording.
- **Cards:** add a card, search **Tapo**. All six have a visual editor.
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
