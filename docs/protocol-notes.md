# H500 protocol notes

What has been established against a physical H500 with two paired TD21
doorbells. Everything here was observed, not inferred — guesses are labelled as
such. `tools/probe_live.py` reproduces all of it.

## Request shapes

Two forms work, and they are not interchangeable.

**Direct, namespace beside the method.** Used for reads:

```json
{"method": "get", "app_component": {"name": "app_component_list"}}
```

**Wrapped in `multipleRequest`.** What `pytapo.executeFunction` sends, and the
only form that returns a per-method `error_code`:

```json
{"method": "multipleRequest",
 "params": {"requests": [{"method": "getGeneralDeviceList",
            "params": {"general_camera_manage": {"paired_general_device_list": {}}}}]}}
```

Cameras are addressed **inline**, with `child_device_id` and `child_device_mac`
inside a camera namespace. The `controlChild` envelope that python-kasa uses for
hub children returns `-50021 "This model is not supported."` for every method,
including ones that certainly exist — it is for Sub-1GHz devices, not
`SMART.IPCAMERA` ones.

## Error codes observed

| Code | Where | Meaning |
| --- | --- | --- |
| `-40106` | per-method | No such method, or no such (namespace, section) pair |
| `err_code 40210` | top level | Envelope rejected; empty params do this |
| `-50021` | `controlChild` | Wrong child mechanism for a camera |
| `-40401` | login | Refused; seen with inner `-60502`, undocumented |

`-40106` is not a clean oracle. A real namespace with a wrong section name
returns it too, so a negative proves nothing about either half.

## Hub modules

`{"method": "get", "app_component": {"name": "app_component_list"}}` returns 47.
Media-adjacent ones, all version 1 unless noted:

`generalCameraManage`, `hubPlayback`, `hubRecord` v2, `playback` v6,
`playbackDelete`, `preWakeUp`, `recordDownload` v2, `ringLog`, `snapshot` v2,
`eventCenter` v2, `aovStorage`.

Three of these contradict assumptions the integration shipped with:

- **`playbackDelete` exists.** The integration's `delete_recording` only removes
  the downloaded copy, on the belief that no per-clip hub delete exists. That
  belief came from pytapo having no such method and TP-Link's docs saying
  hub footage can only be cleared by formatting. The hub says otherwise. The
  call shape is not yet known.
- **`ringLog` and `eventCenter` exist.** The coordinator polls the indexed clip
  list for doorbell events, which trails the actual press. Either module is
  likely a better source.
- **`snapshot` v2 exists.** Thumbnails are currently ffmpeg frame extractions
  from downloaded clips.

There is **no** `live`, `preview`, or `stream` module. Live view is likely not a
hub module at all — plausibly camera-direct after a `preWakeUp`, or not exposed
locally. Unproven either way.

## The empty nonce

On firmware 1.3.20 `getMediaEncrypt` returns `enabled: "on"`, and the
port-8800 `Key-Exchange` header is:

    cipher="AES_128_CBC" username="admin" padding="PKCS7_16" algorithm="MD5" nonce=""

The nonce is **present but empty**. `pytapo` passes its `if b"nonce" not in
key_exchange` check and then dies on `if not nonce` one layer down, so every
download fails with `NonceMissingException`.

Credentials are not the cause. The media session's digest auth hashes the
*cloud* password (`hashed_password = pwd_digest(cloud_password, ...)`) and that
exchange returned HTTP 200, so the cloud password is verified correct before
the nonce is ever read.

An empty nonce is not fatal: the key is `md5(nonce + b":" + hashed_password)`
and the IV is `md5(username + b":" + nonce)`, both defined for `b""`. Letting
it through produces a stream that decrypts correctly — confirmed with
`ffprobe`: MPEG-TS, H.264, 2304x1296. `api.py` carries the empty value into
key derivation rather than reimplementing pytapo's crypto.

`pytapo` only skips the nonce when `username == b"none"`, which is its signal
for encryption being off. This hub says `username="admin"` *and* sends no
nonce, a combination pytapo has no branch for.

## Waking a camera for live view

The oracle is valid when a `multipleRequest` carries **non-empty** params: a
known-good call succeeds, an impossible name returns a per-method `-40106`.
Both were verified before trusting any negative below. With empty params the
envelope is rejected wholesale and nothing is learned — an earlier run made
that mistake and reported 48 of 48 names present.

`preWakeUp` is a **component, not a method**: `multipleRequest[preWakeUp]`
returns `-40106`. So does every one of these:

    getPreWakeUpConfig  getPreWakeUpStatus  getPreWakeUp  preWakeUpDevice
    preWakeUpChild      preWakeUpGeneralDevice  getWakeUpStatus
    preLive  preVod  preDownload  prePlayback  preLiveStream  prePreview
    getGeneralDeviceStatus  getGeneralDeviceInfo  getGeneralDeviceCapability
    getPreviewStatus  getLiveStatus  getLiveStreamInfo  getStreamInfo
    getRingLog  searchRingLog  getEventList  searchEventList
    getSnapshotConfig  getSnapshotUrl  getSnapshotList

The hub also exposes no introspection: `getModuleSpec`, `getFunctionList`,
`getMethodList`, `getApiList`, `getSupportedMethods`, `getCapability`,
`getDeviceCapability`, `getFeatureList` are all absent, as are `get` against
`function`, `module_spec`, `api_spec`, `capability` and `support`. There is no
way to make the hub list its own method table.

Two things worth keeping in mind:

- Downloads work against a sleeping TD21 with no wake step at all, so waking
  may be needed only for live.
- `preWakeUp` being a *hub* component suggests the hub wakes the camera on the
  app's behalf during some other call, rather than exposing a standalone verb.
  If so, opening a live media session may itself trigger the wake — which the
  port-8800 probe has never validly tested, because the run where `preview`
  returned HTTP 401 had its `type=download` control return 401 too.

## What has not worked

- Bare method names with `{}` params: envelope rejected, `40210`, method never
  evaluated.
- Section-name brute force: 160 `get` combinations across 16 namespaces
  returned exactly one hit (`harddisk_manage`). The space is too large to
  search blind.
- `getWakeUpConfig`, `getComponentList` on the hub: genuine `-40106`.

## Operational limits

The hub is easy to wedge and recovers on its own timescale, not on demand.

- Repeated failed media-session auth left port 8800 **refusing TCP** for
  several minutes.
- Repeated logins from separate processes left the control channel refusing
  with `-40401` / `-60502` for several minutes. Not a password lockout: pytapo
  reports `Temporary Suspension: Try again in N seconds` when the hub sends
  `sec_left`, and it did not.

`pytapo`'s `authenticate()` is `if not self.stok: refresh`, so one process holds
one login for its lifetime. Use `--batch` for many experiments rather than one
process per experiment. Home Assistant already holds a single client per config
entry, so the integration itself does not have this problem.

## Where to go next

Blind probing has stopped paying. Two routes remain, in order of cost:

1. **Firmware strings.** TP-Link publishes H500 firmware. `binwalk` plus
   `strings` over the extracted filesystem should yield the method and
   namespace tables directly, with no hub load at all.
2. **App capture.** Port 8800 is raw TCP with its own AES, not TLS, so there is
   no pinning to defeat; the session nonce is in the capture and the key inputs
   (cloud password, `superSecretKey`) are already known. Decrypt offline with
   pytapo's own crypto.
