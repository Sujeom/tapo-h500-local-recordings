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

## Log in as `admin`, not as the cloud email

The hub accepts the **camera account** username and refuses the TP-Link cloud
email, and the refusal is indistinguishable from a lockout unless you read the
debug log:

```
{'error_code': -40401, 'result': {'data': {'code': -60502}}}
username: 'someone@example.com'
```

There is no `sec_left` in that reply, so pytapo never raises its
`Temporary Suspension` message — it raises the generic
`Invalid authentication data` instead. That is the same `-40401` / `-60502`
pair recorded in the error table, and it looks exactly like the wedge that
repeated logins cause. It is not one: on 2026-08-13 the email was refused after
eight hours of complete quiet, and `admin` connected on the first try in the
same minute.

**How to tell the two apart.** A real wedge refuses *every* credential and
clears with time or a power cycle. A wrong username refuses forever and clears
the moment the username changes. If a login fails, try `admin` before
concluding the hub is wedged — and before retrying, since a refused login is
itself a login attempt.

`tools/*.py` read `TAPO_USERNAME` and fall back to `admin`. A `.env` that sets
it to the cloud email overrides that fallback, which is how this went unnoticed.

## Every component, probed

All 47 components, both addressing forms, 709 read-only calls in one session.
Twelve calls answer with anything other than `-40106`:

| Component | Call | Result |
| --- | --- | --- |
| `faceDetection` | `getFaceDetectionConfig` | `0` — `enabled` plus a `tags` list |
| `led` | `getLedStatus`, and `get led.*` in any section | `0` |
| `siren` | `getSirenConfig`, `getSirenStatus` | `0` |
| `usrDefAudio` | `getUsrDefAudioConfig` | `0` — 5 files max, 15000ms each |
| `usrDefAudio` | `getUsrDefAudioList` | `0` — all five slots empty |
| `mirrorscreen` | `getMirrorScreenConfig` | `-40209`, or `0` with the right shape |
| `migrate` | `getMigrateConfig` | `-40209` |

The other 40 components answer `-40106` to every namespace spelling and every
`get<Component>{Config,Status,Info,List,}` name. A component in
`app_component_list` is not a promise of a local method, and this is the
measurement that settles it rather than assuming it.

### faceDetection is readable — the earlier note was wrong

```json
{"face_detection": {"detection": {"enabled": "on",
 "tags": ["family","friend","courier","neighbor","colleague","schoolmate","others"]}}}
```

That contradicts the old finding below that "every plausible method is absent".
It is read-only: `setFaceDetectionConfig` refuses even a write of the hub's own
current value with `-40211`, and the value was confirmed unchanged afterwards.
Exposed as a diagnostic `binary_sensor`, with the tags as an attribute.

### mirrorscreen is reachable, and empty

`-40209` is not constant here — params genuinely matter, which distinguishes it
from `getRecordPlan`'s `-60305`:

| Params | Result |
| --- | --- |
| `{"mirrorscreen":{"name":["config"]}}` | **`0`** — `{"mirrorscreen": {}}` |
| `{"mirrorscreen":{"name":"config"}}` | `-40209` |
| `{"mirrorscreen":{"name":"zzz_nope"}}` | `-40209` |
| `{"mirrorscreen":{}}` | `-40209` |
| `{"zzz_nope":{"name":"config"}}` | `-40106` (control) |

So `name` must be a **list**. The call then succeeds and returns an empty
object — the component is addressable but holds no configuration, so there is
nothing to expose and nothing to control. Worth retrying if a screen is ever
paired to the hub.

## The full component inventory

All 47, for reference — earlier notes only listed the media-adjacent subset,
which is how `mirrorscreen` went unnoticed:

    AIEnhance          account            aovStorage         audioSourceCapability
    childControl       childInherit       childQuickSetup    chime
    dataDownload       dateTime           deviceLoad         deviceShare
    diagnose           eventCenter        faceDetection      faceTracking
    firmware           generalCameraManage hardDisk          hubPlayback
    hubRecord          iotCloud           led                localSmart
    matter             matterControl      migrate            mirrorscreen
    multiLensCamMgmt   playback           playbackDelete     preWakeUp
    quickSetup         recordDownload     ringLog            setDetailLanguage
    siren              snapshot           subg               supportRE
    support_presence_sensor               system             testChildSignal
    testSignal         tssDeviceManage    usbsharemanage     usrDefAudio

### mirrorscreen: the method exists, the params do not

`getMirrorScreenConfig` answers **`-40209`**, not `-40106`. That distinction is
the whole finding: `-40106` is "no such method" and `-40209` is the parameter
complaint this firmware also returns for an out-of-range siren volume. So the
method is real and the params tried so far are wrong.

Absent so far: every direct-namespace form (`mirrorscreen`, `mirror_screen`,
`mirrorScreen` across `config`/`info`/`status`/`list`/`enable`), and the
sibling names `getMirrorscreenConfig`, `getMirrorScreenStatus`,
`getMirrorScreen`, `getScreenMirrorConfig`, `getMirrorScreenList`.

Untried, because the hub started refusing logins mid-probe: `getMirrorScreenConfig`
with an empty namespace, a `name` list, a `table`, a channel, or child device
addressing. pytapo has no casting or mirroring concept at all to borrow a shape
from, so this one has no reference implementation.

Worth noting the name is ambiguous. It may mean casting a feed to another
screen, or it may mean flipping the image — "mirror" in the camera-settings
sense. Nothing observed yet distinguishes the two.

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

There is **no** `live`, `preview`, or `stream` module. That turned out not to
matter: live view is not a hub *module* at all, it is a port-8800 media session
like a download, and one is now known to open. See *Live view: the session
opens*.

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

### Live view: the session opens

**Confirmed on firmware 1.3.20.** The shape came from pytapo's hub-child code
path and was then verified against the hub. What is confirmed is that the hub
*accepts* a live request and allocates a session; whether video frames follow
is still open — see the end of this section.

pytapo keeps a separate code path for a child device, taken whenever `childID`
is set. `Tapo.getMediaSession(StreamType.Stream)` builds the query string:

```python
{"deviceId": childID, "playerId": playerID, "type": "video"}   # no media_type
```

and `Streamer._build_preview_payload()` sends a **`preview`** block:

```json
{"type":"request","seq":1,"params":{"method":"get","preview":{
  "audio":["default"],"channels":[0],"resolutions":["HD"],"deviceId":"..."}}}
```

Three things follow, and each contradicts how live has been probed so far:

- **The query type and the payload block name differ.** Live is query
  `type=video` carrying a `preview` block. Every previous run varied both
  together as one word, so this combination was never sent — which is why the
  live attempts read as inconclusive rather than as negatives.
- **No `media_type` on a live query.** The download query needs it; this one
  does not.
- **The child download type is `sdvod`, not `download`.** The H500 nonetheless
  accepts `download`, which is the verified path, so this is noted only because
  it shows the hub tolerates more than one spelling.

#### What the hub actually answered

| Query type | Payload block | Identity | Result |
| --- | --- | --- | --- |
| `video` | `preview` | `dev_id`+`mac` | `error_code: 0`, `session_id: "10"` |
| `video` | `preview` | `deviceId` | `error_code: 0`, `session_id: "11"` |
| `preview` | `preview` | `dev_id`+`mac` | `HTTP ERROR 401` |
| *(any, after the 401)* | | | port 8800 refusing TCP |

So **query `type=video` carrying a `preview` block is accepted**, and the hub
allocates a live session for it. Two further things fall out:

- **Identity does not matter.** Both conventions were accepted, so the hub
  resolves the camera either way. The integration can keep using the inline
  `dev_id` + `mac` form it already uses for downloads.
- **The query type is what gets authenticated.** `type=preview` never reached
  the payload — it failed at the HTTP layer with a 401, and port 8800 then
  refused TCP, which is the documented wedge. Nothing but `type=video` should
  ever be sent again; the other spellings cost a wedged hub to learn nothing.
  `tools/probe_live.py` no longer offers them.

#### The session opens, talks, and then sends no video

Measured after fixing the probe — it used to return on the first non-video
response, so it stopped at the success acknowledgement and reported an accepted
session as a dead one, the same acknowledgement `iter_recording` has always
looped straight past. With the read continuing:

| Query type | Block | Result |
| --- | --- | --- |
| `video` | `preview` | opens, then `channel_lens_mask_info` notification, **no video in 90s** |
| `video` | `video` | no response at all, times out |

So `preview` is the right block — it is the only one the hub answers — and the
hub is engaged, not ignoring us: after the `error_code: 0` acknowledgement it
volunteers a real live-session notification,

```json
{"type":"notification","params":{"event_type":"channel_lens_mask_info",
 "channels":0,"enabled":"off"}}
```

and then goes quiet. Ninety seconds is far past any plausible wake, so this is
not a slow camera.

Three explanations have been eliminated:

- **Not the wake verb.** Every `preWakeUp` spelling returns `-40106` in the
  *direct* form too — `{"method":"get","preWakeUp":{"name":"config"}}` and
  variants, plus `pre_wake_up`, `wake_up`, `battery` and `power`. The earlier
  negatives were all `multipleRequest` method names, so this closes the other
  half of that question. There is no locally reachable wake.
- **Not the channel.** The paired-camera records carry **no** `channel_id`
  field at all, so both cameras are channel 0 and are told apart by `dev_id`.
  `channels: [0]` is correct, which is also why downloads work for both.
- **Not the identity convention.** Both were accepted.

What remains is that a hub-attached battery TD21 does not appear to stream to a
local client at all — the hub takes the session and the camera never wakes for
it. Downloads work against a sleeping camera precisely because the hub serves
them from its own disk and the camera is not involved.

The next real evidence would be an app capture of the window between the
acknowledgement and the first video frame: whatever the app sends there is the
missing step, and port 8800 is raw AES rather than TLS, so it decrypts offline
with pytapo's own crypto.

Re-run with a held login — a fresh login per attempt is what wedges the hub,
and check `probe_live.py --check` first if a 401 has just happened:

    tools/h500_session.py --host <ip> &
    tools/h500_session.py --live --camera 1

## Detections and AI classification

**This section was wrong, and the correction is the most useful thing in this
file.** `searchDetectionList` works, returns a type for every recording, and
even carries face IDs. What follows is what it actually does; the old negative
findings are kept below it because they explain how the mistake was made.

### searchDetectionList classifies every clip

Verified on firmware 1.3.20 over a three-day window: **26 clips, 26 matching
detections, none unmatched.** The join key is exact — the detection's
`start_time` equals the clip's `startTime`.

```json
{"alarm_type": 20, "device_mac": "", "end_time": 1786542147,
 "event_start_time": 1786542131, "events_1": 524450, "start_time": 1786542131,
 "event_info": [{"face_bitmap": 0, "face_id": 272465657857}]}
```

`events_1` is a **bitmask of everything that fired at once**, and `alarm_type`
is always its highest set bit plus one — checked against every observed record:

| alarm_type | events_1 | highest bit | | alarm_type | events_1 | highest bit |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 2 | 1 | | 17 | 66050 | 16 |
| 6 | 34 | 5 | | 19 | 262178 | 18 |
| 8 | 130 | 7 | | 20 | 524450 | 19 |
| 9 | 256 | 8 | | 22 | 2097442 | 21 |

So the mask is the richer field: `2097442` is bits 1, 5, 8 and 21 — four
concurrent detections where `alarm_type` reports only the last.

**Only two codes are named**, and only where the evidence carries: `2` is set
on nearly every detection, and `20` is the only code observed carrying a
`face_id`. The rest are real and unnamed, and are displayed as `type 22` rather
than guessed at. The hub cannot name them either — `getAlertTypeList` is
`-40106` and `getAlertConfig` returns `{}`.

**Which code means a doorbell press is still unknown.** `RING_ALARM_TYPES` in
`const.py` is deliberately empty; add the code there once a real press has been
captured and the event entity, the download filter and every card pick it up at
once.

### Why it looked dead for so long

Two mistakes, one in the notes and one in the code:

- **The window was blamed wrongly.** A seven-day window returns 26 detections
  here. Window size was never the problem.
- **The integration disabled the call on its first quiet poll.** A window with
  no detections answers `{}`, not an empty list, so
  `result["playback"]["search_detection_list"]` came back `None`, failed the
  `isinstance(..., list)` check, and set `_detection_supported = False` for the
  rest of the session. One quiet startup poll turned the feature off
  permanently and nothing ever retried it. An empty reply is an answer, not a
  refusal — it is now treated as one.

The lesson is narrower than "the hub hides this": a self-disabling fallback
turned a working call into a permanent negative, and the negative got written
down as a property of the hardware.

### The old negative findings

These were real observations and are kept for the record, but note that the
`searchDetectionList` line below is the one now known to be wrong. Tested on
firmware 1.3.20:

- `faceDetection` and `faceTracking` are components, but every plausible method
  is absent (`-40106`): `getFaceDetectionConfig`, `getFaceTrackingConfig`,
  `getFaceList`, `getFaceInfo`, `getAIEnhanceConfig`, `getLocalSmartConfig`,
  `getSmartDetectionConfig`, `getPersonDetectionConfig`, `getDetectionConfig`,
  `getAlertEventType`.
- ~~`searchDetectionList`~~ **(wrong — see above)** is accepted — `error_code: 0` — but returns `{}` for
  both cameras across a seven-day window, with and without child addressing.
  It is a live method that yields nothing here.
- Every clip is `video_type: "2"`, whatever triggered it. There is no per-clip
  classification, which is the same reason a doorbell press cannot be told
  apart from motion.
- Person, pet and vehicle config getters are camera-level, and a camera child
  cannot be addressed (see `controlChild` above).

One thing worth ruling out before calling `searchDetectionList` dead. pytapo
does not pass it UTC: `getEvents` shifts the window by a *clock correction*,

    timeCorrection = host_now - hub["system"]["clock_status"]["seconds_from_1970"]

and applies it to that call **and to nothing else** — the video searches get raw
UTC, which is why `searchVideoWithUTC` works here while this one returns `{}`.
So the two calls may simply not share a time base, and the integration passes
both the same UTC window.

**Measured, and it is not the answer.** `getTime` returns

```json
{"system": {"basic": {"timing_mode": "ntp", "epoch_sec": 1786585040}}}
```

which was **20 seconds** behind the host — the hub runs NTP and keeps good
time. No offset of that size moves a seven-day window, so the time base is
ruled out and `searchDetectionList` really does yield nothing here. The
integration is right to pass UTC and right to disable the call after one
rejection.

A clock this accurate also means clip timestamps and the media browser's date
folders can be trusted.

`getTime` returns `system.basic.epoch_sec`, which is *not* the
`system.clock_status.seconds_from_1970` that pytapo's `getTimeCorrection` looks
for — but `getClockStatus` is a separate working method that returns exactly
that, plus `local_time`. See *The method sweep, done properly*.

The cameras report `ai_camera_support: 15` and `ai_hub_support: 15`, a bitmask
for four AI features, so the classification happens on the device. The hub just
never hands it to a local client.

This is why the coordinator treats `searchDetectionList` as best-effort: it
tries once, gets a non-list back, disables itself and polls the clip index
instead.

## The method sweep, done properly

The earlier sweep sent `{}` for params, which gets the envelope rejected with
`40210` before the method is ever evaluated — so it proved nothing, as noted
above. pytapo carries the real param shape for every method it implements, so
the sweep was redone with params the hub actually parses: 76 read methods plus
55 direct-form namespace probes, over one held login.

**28 methods answer.** The ones the integration did not already use:

| Method | Params | Returns |
| --- | --- | --- |
| `getSirenConfig` | `{"siren":{}}` | `{"siren_type":"Doorbell Ring 5","volume":"8","duration":300}` |
| `getSirenTypeList` | `{"siren":{}}` | 19 sounds: Doorbell Ring 1-10, Phone Ring, Dripping Tap, Alarm 1-5, Connection 1-2 |
| `setSirenStatus` | `{"siren":{"status":"on"\|"off"}}` | accepted, `error_code 0` |
| `setSirenConfig` | `{"siren":{"volume":"8",…}}` | accepted; volume **1-10**, 0 and 11 give `-40209` |
| `getClockStatus` | `{"system":{"name":"clock_status"}}` | `seconds_from_1970` **and** `local_time` |
| `getTimezone` | — | `{"zone_id":"America/New_York","timezone":"UTC-05:00"}` |
| `getDstRule` | — | full DST rule |
| `getReboot` | — | scheduled reboot: `{"enabled":"off","day":"0","time":"03:00:00"}` |
| `getFirmwareAutoUpgradeConfig` | — | `{"enabled":"on","time":"03:00"}` |
| `getDiagnoseMode` | — | `{"diagnose_mode":"off"}` |
| `getDeviceInfo` | `{"device_info":{"name":["basic_info"]}}` | model, `sw_version`, hardware |

Three methods are **present but want different params**: `getDayNightModeConfig`
(`60805`), `getRecordPlan` (`-60305`), `getUserID` (`60705`). A non-`-40106`
code means the method exists, so these are shapes worth revisiting.

`getClockStatus` supersedes the note above about pytapo's `getTimeCorrection`:
this hub *does* expose `system.clock_status.seconds_from_1970`, it is simply not
what `getTime` returns.

### Which setters the hub accepts

Proven without changing anything: read the current value, write that exact
value back, read again. `error_code 0` proves the setter exists and is
accepted; the re-read proves nothing moved.

| Setter | Params | Result |
| --- | --- | --- |
| `setLedStatus` | `{"led":{"config":{"enabled":"on"}}}` | accepted |
| `setCircularRecordingConfig` | `{"harddisk_manage":{"harddisk":{"loop":"on"}}}` | accepted |
| `setDiagnoseMode` | `{"system":{"sys":{"diagnose_mode":"off"}}}` | accepted |
| `setFirmwareAutoUpgradeConfig` | `{"auto_upgrade":{"common":{…}}}` | accepted |
| `setSirenStatus` | `{"siren":{"status":"off"}}` | accepted |
| `setSirenConfig` | `{"siren":{"volume":"8"}}` | accepted; volume 1-10 |
| `getCoverConfig` / `setRecordAudio` | — | getter absent, so not writable here |

`setFirmwareAutoUpgradeConfig` replaces the whole `common` block, so a toggle
must send back the `time` and `random_range` it is not changing or the schedule
is wiped. `status.auto_upgrade_config` exists for exactly that and is tested.

Three were **not** probed, on purpose. `setReboot` is in the probe's
`NEVER_SEND` list and its params (`timing_reboot`) are ambiguous between
scheduling a reboot and performing one; a wrong guess reboots the hub mid-write.
`setMediaEncrypt` would break the download path that took the empty-nonce work
to get right. `setTimezone` would shift every clip timestamp and the folder
names derived from them.

### The control surface is now fully mapped

Re-run keeping every result and cross-referenced against pytapo's setter table:
**every method that answers and has a setter is either exposed in the
integration or excluded above.** There is no remaining reachable control.

The 22 methods that answer: `getChildDeviceList`,
`getChildDeviceComponentList`, `getCircularRecordingConfig`, `getClockStatus`,
`getDeviceInfo`, `getDeviceIpAddress`, `getDiagnoseMode`, `getDstRule`,
`getFirmwareAutoUpgradeConfig`, `getFirmwareUpdateStatus`, `getLedStatus`,
`getMediaEncrypt`, `getReboot`, `getSdCardStatus`, `getSirenConfig`,
`getSirenStatus`, `getSirenTypeList`, `getThirdAccount`, `getTimezone`,
`searchDateWithVideo`, `searchDetectionList`, `searchVideoWithUTC`.

Corrections to the earlier count, both from a sweep classifier that read a
direct-form reply as a success: `getAudioConfig` is **absent** — its raw reply
is `{"method":"get","error_code":-40106}` — so there is no speaker volume or
record-audio control. And the five `msg_alarm` "hits" were namespace probes,
not methods.

Three getters answer with no setter worth having: `getDstRule` and
`getThirdAccount` are read-only (the latter returns the cloud account's
username and public key and should not be surfaced as an entity), and
`getFirmwareUpdateStatus` is a status.

Two methods exist but their feature does not:

- `getRecordPlan` returns `-60305` and `getDayNightModeConfig` returns `60805`
  **for every param shape tried**, including a bare name. A constant code
  regardless of params is not a wrong-shape complaint, so there is no schedule
  or night-vision control to find here.
- Both are camera-level features, and this hub has no addressable camera child:
  `getChildDeviceList` answers `{"start_index":0,"sum":0}`. **Zero children.**
  That is the real reason `controlChild` returns `-50021` — the cameras live in
  `general_camera_manage`, not in `childControl` at all.

### The components that are advertised but unreachable

`playbackDelete`, `snapshot`, `eventCenter` and `ringLog` all appear in
`app_component_list`, and **none of them can be reached**. Every namespace was
probed in the direct form that works for `app_component` and
`general_camera_manage`, across five section spellings (`config`, `info`,
`status`, `list`, and the namespace's own name):

    playback_delete  snapshot  event_center  ring_log
    hub_record  hub_playback  record_download  aov_storage  ring  event

Every one returned `-40106`. pytapo has no delete-recording and no snapshot
method at all to borrow a shape from, and the method-name route was already
exhausted (`getRingLog`, `searchRingLog`, `getEventList`, `searchEventList`,
`getSnapshotUrl`, `getSnapshotList` are all absent).

`msg_alarm` is the one exception: it is a real namespace — it does not
`-40106` — but every section returns `{}`, the same accepted-and-empty pattern
as `searchDetectionList`.

So the conclusions the integration shipped with stand, now for a much better
reason than pytapo's silence:

- **No per-clip hub deletion.** `playbackDelete` exists as a component and is
  not addressable, so `delete_recording` removing the downloaded copy remains
  correct.
- **No hub snapshot.** Thumbnails stay ffmpeg frame extractions.
- **No faster event source.** `eventCenter` and `ringLog` are unreachable, so
  polling the clip index remains the only path.

A delete verb was deliberately **not** brute-forced. Any probe specific enough
to prove one exists is specific enough to erase a recording, and hub footage
cannot be recovered.

## What has not worked

- Bare method names with `{}` params: envelope rejected, `40210`, method never
  evaluated.
- Section-name brute force: 160 `get` combinations across 16 namespaces
  returned exactly one hit (`harddisk_manage`). The space is too large to
  search blind.
- `getWakeUpConfig`, `getComponentList` on the hub: genuine `-40106`.

## Previews without downloading

The `snapshot` component is unreachable, so there is no hub-side still image.
There is a cheaper route to the same result: the download session takes a
`start_time`/`end_time` window, so asking for the first couple of seconds and
abandoning the stream yields enough MPEG-TS to decode one frame.

Measured on firmware 1.3.20, camera index 1:

| Window | Fetched | Elapsed | Result |
| --- | --- | --- | --- |
| 2s | 232,368 B | 2.2s | 640x360 JPEG, 27 KB |
| 5s | 232,368 B | 4.0s | identical |

Both stopped at the byte cap rather than the window, so the **byte bound is
what actually binds** — which is why the integration bounds on both. For scale,
a full 15-second clip is 3,398,852 bytes, so a preview costs roughly 7% of a
download.

Breaking out of `iter_recording` early is clean: closing the async generator
raises `GeneratorExit` at the `yield`, which unwinds the `async with` for both
the media session and the client lock. The `IncompleteRecordingError` at the end
is never reached, so an abandoned preview does not surface as a failed download.

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

Blind probing has stopped paying, but one targeted probe is now worth more than
any amount of it:

Blind probing is finished for live view. The verb is known — query `type=video`
with a `preview` block opens a session — and the remaining gap is that the
camera never streams into it, with no locally reachable wake to fix that. See
*Live view: the session opens*. Two routes remain, in order of cost:

1. **Firmware strings.** TP-Link publishes H500 firmware. `binwalk` plus
   `strings` over the extracted filesystem should yield the method and
   namespace tables directly, with no hub load at all.
2. **App capture.** Port 8800 is raw TCP with its own AES, not TLS, so there is
   no pinning to defeat; the session nonce is in the capture and the key inputs
   (cloud password, `superSecretKey`) are already known. Decrypt offline with
   pytapo's own crypto.
