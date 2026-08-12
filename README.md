# Tapo H500 Local Recordings for Home Assistant

Experimental HACS custom integration for listing and downloading recordings stored on a Tapo H500 HomeBase.

## What works

- Connects directly to the H500 on your LAN.
- Lists paired hub-managed cameras/doorbells.
- Lists indexed H500 recordings for a date range.
- Downloads an exact indexed recording into Home Assistant's local media directory.
- Downloaded clips appear under **Media → Local media → tapo_h500**.
- Does **not** call `preWakeUp`, `preVod`, or a TP-Link cloud media endpoint.

The TP-Link cloud-account password is still required by Tapo's **local** port-8800 media encryption handshake. It is stored in the Home Assistant config entry and is not placed in filenames, service responses, or logs.

## Scope and limitations

This first release deliberately supports only the path verified on an H500 with paired TD21 battery doorbells. It does not provide live view, notifications, event automation, thumbnail browsing, deletion, or MP4 conversion. Downloads are MPEG-TS (`.ts`) with MIME type `video/mp2t`.

The protocol is undocumented. Pinning the H500 to a stable LAN address is strongly recommended. Firmware changes may require integration updates.

## HACS installation

HACS installs integrations from a GitHub repository. After this repository is uploaded to GitHub:

1. In HACS, open **Integrations**.
2. Open the three-dot menu and choose **Custom repositories**.
3. Enter the GitHub repository URL and choose category **Integration**.
4. Install **Tapo H500 Local Recordings**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration**.
7. Search for **Tapo H500 Local Recordings**.

For a manual installation, copy `custom_components/tapo_h500` into Home Assistant's `custom_components` directory and restart.

## Configuration

The config flow asks for:

- **H500 IP address**
- **Camera account username** (normally `admin`)
- **Camera account password**
- **TP-Link cloud account password** (used only to derive local media encryption keys)

The H500 and Home Assistant must be able to reach each other over the LAN. The integration uses HTTPS/control traffic to the hub and TCP port `8800` for recording downloads.

## Usage

The integration exposes two response-capable actions under **Developer tools → Actions**.

### 1. List recordings

Action: `tapo_h500.list_recordings`

```yaml
config_entry_id: YOUR_CONFIG_ENTRY_ID
camera_index: 1
start_date: "20260812"
end_date: "20260812"
```

Dates use `YYYYMMDD` in UTC. If dates are omitted, today in UTC is used. The response includes exact `start_time` and `end_time` values. Camera indexes follow the paired-device order reported by the hub and begin at zero.

### 2. Download one recording

Action: `tapo_h500.download_recording`

```yaml
config_entry_id: YOUR_CONFIG_ENTRY_ID
camera_index: 1
start_time: 1786553183
end_time: 1786553198
```

Always copy the exact time boundaries from `list_recordings`. A successful response includes:

```yaml
media_content_id: media-source://media_source/local/tapo_h500/Side_Doorbell_1786553183.ts
path: tapo_h500/Side_Doorbell_1786553183.ts
bytes: 3398852
```

Open **Media → Local media → tapo_h500** to play or download the clip.

## Security notes

- This is a local, read-only recording integration; it does not delete clips or modify hub settings.
- Passwords are kept in the Home Assistant config entry.
- Device IDs and MAC addresses are not returned by service actions.
- Do not expose TCP port `8800` to the internet.

## Verification performed

- Unit tests cover the H500 request payload, required `Content-Length: 0` framing override, and safe media filenames.
- The integration protocol client was tested with stock `pytapo==3.4.18` against a physical H500; `pytapo` supplies transport and crypto while this integration supplies the app-derived H500 request/framing.
- A bounded TD21 recording download reached the explicit finished notification and returned 3,398,852 bytes.
- `ffprobe` identified MPEG-TS, H.264 video, and a 15.07-second duration.

## License

MIT. The integration depends on the separately distributed MIT-licensed `pytapo` package.
