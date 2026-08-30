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
**It is writable after all.** `-40211` was a parameter complaint, not a
refusal — the same trap as `mirrorscreen`'s `-40209`. The hub accepts only the
*whole* detection block:

| Params to `setFaceDetectionConfig` | Result |
| --- | --- |
| `{"detection":{"enabled":"on","tags":[...]}}` | **`0`** |
| `{"detection":{"enabled":"on"}}` | `-40211` |
| `{"name":"config","detection":{...}}` | `-40211` |
| `{"config":{"enabled":"on"}}` | `-40211` |
| `{"enabled":"on"}` | `-40211` |

Same shape rule as `setFirmwareAutoUpgradeConfig`: send the block back whole or
it is rejected. Verified live by toggling off and on again — the seven tags
survived both writes and the hub finished where it started. Exposed as a
`switch`, and `status.face_detection_config` carries the tags through every
toggle.

### The app's face summary is cloud-side, not on the hub

Asked directly whether the hub can produce the app's recognised-faces summary:
**no**, and this one is a clean negative rather than a guessing failure.

22 plausible method names — `getFaceRecognitionConfig`, `getFaceAlbum`,
`getFaceCollection`, `getVisitorList`, `getStrangerList`, `getPersonList`,
`getAiDetectionConfig`, `getSmartDetectionConfig`, `searchFaceEvent`,
`getFaceThumbnail`, `getFaceImage` and others — were each tried against four
namespaces (`face_detection`, `localSmart`, `local_smart`, `playback`). All 88
combinations returned `-40106`.

That uniformity is the evidence. Every method on this hub that exists but is
being called wrongly answers `-40209` or `-40211` — that is how the face
detection setter and the mirrorscreen shape were both found. Not one such reply
appeared here, so these methods are absent rather than merely mis-shaped.

The division of labour that fits: the hub *recognises* (it assigns a stable
`face_id` per person) and TP-Link's cloud *identifies* (it holds the name and
the photo). The hub carries the account link — `getThirdAccount` returns the
cloud username and a public key — but `getCloudConfig` holds only
`upgrade_info`, and no local call reaches the face data.

So a local integration can report *that* a known face was seen and *which* id
it was, and nothing more. Names and photos would need the cloud API, which is a
different architecture from this one.

### No battery level, re-checked and still no

Re-probed with the `admin` login and the `-40209`/`-40211` rule, since both
were missing when this was first written off.

`general_camera_manage` is a namespace that certainly works, so it was asked
for nine different sections — `general_device_status`, `battery_info`,
`device_status`, `battery`, `device_list_detail` and others. Every one returned
`error_code 0`, which looks like a hit until you compare the payloads:
`battery_info` returns **the same object** as `paired_general_device_list`. The
hub ignores the section name here, exactly as `led` and `getFaceDetectionConfig`
do, so a `0` proves nothing on its own.

The per-camera record has 16 fields and not one is battery, power, charge,
voltage or percentage:

    AI_enhance_enabled  ai_camera_support  ai_enhance     ai_hub_support
    alias               backup_wifi        category       device_id
    device_model        device_type        hub_storage_enabled  mac
    network_mode        parent_device_id   plan_24h_record      wifi_backup_enabled

And 11 battery method names — `getBatteryStatus`, `getBatteryInfo`,
`getBatteryConfig`, `getBatteryCapability`, `getPowerMode`, `getChargingMode`,
`getBatteryStatistic`, `getDeviceLoad`, `getGeneralDeviceStatus`,
`getGeneralDeviceInfo`, `getChildBatteryInfo` — across four namespaces, all
`-40106`, with none of the `-40209`/`-40211` replies that mean "exists, wrong
params".

The reading lives on the camera, and `getChildDeviceList` answers `sum: 0`, so
there is no child to address. Same shape as the face names: the hub relays what
it holds, and the battery is not one of those things.

### What the alarm_type codes mean

Ground truth at last for the one that mattered. The front doorbell was rung at
14:42:25 on 2026-08-13; it was the only event on that camera in six hours:

```
alarm_type=17  events_1=66080  types=[6, 10, 17]  faces=[]
```

**17 is a doorbell press.** `RING_ALARM_TYPES` is now `{17}`, which turns on
the ring classification for the event entity, the presses-only download filter
and every card at once.

Measured across 35 distinct detections on both cameras over seven days:

| Code | Seen | Alone | With a face | Reading |
| --- | --- | --- | --- | --- |
| 2 | 31 | 1 | 5 | **motion** — the base signal nearly everything carries |
| 6 | 29 | 0 | 5 | unknown |
| 22 | 18 | 0 | 1 | unknown |
| 9 | 9 | 1 | 1 | unknown |
| 8 | 8 | 0 | 2 | unknown |
| 20 | 5 | 0 | **5 of 5** | **face** — the only code that ever carries a face_id |
| 17 | 2 | 0 | 0 | **doorbell**, confirmed |
| 10 | 2 | 0 | 0 | rides with 17 every time, never alone |
| 19 | 2 | 1 | 0 | unknown |

The Tapo app then labelled three side-doorbell events on 2026-08-12, and they
differ by exactly one code each, which names two outright:

| App label | Codes | Adds |
| --- | --- | --- |
| motion + person | `[2, 6, 22]` | — |
| person + motion + car | `[2, 6, 8, 22]` | **8 = vehicle** |
| person + dog + motion | `[2, 6, 9, 22]` | **9 = pet** |

That leaves `{2, 6, 22}` covering "motion + person", one code more than labels.
Code 20 (a face) accompanies 6 in all 5 of its detections but 22 only once, and
a face is a person; and the confirmed press carried 6 but not 22, and someone
pressed it. So **6 is person**.

A tamper alarm named the last of the rare codes. The front camera was lifted
off its mount at 11:16:16 on 2026-08-13:

```
alarm_type=19  events_1=786464  types=[6, 19, 20]
```

**19 is theft** — person and face ride along because someone was standing there
doing it. It is also one of only two codes ever seen *alone*, which fits an
alarm that can fire with nobody recognised.

**22 is a face the hub could not identify.** Inferred rather than confirmed by
an app label, but the split is clean:

| Code | Carries a `face_id` | Does not |
| --- | --- | --- |
| 20 | **6 of 6** | 0 |
| 22 | 1 of 18 | 17 |

That single exception is the only event carrying both codes, so the id there
belongs to the 20; excluding it, 22 is 0 for 17. It also cannot be body or
person detection — that would fire on all 29 person events, and 22 fires on 18.
So 20 and 22 partition faces into recognised and not.

**The detection log is immutable, and 20 means matched rather than named.** A
face on the 7:46pm event of 2026-08-12 was given a name in the app afterwards;
the stored detection did not change by a byte — `events_1` is still 524450,
identical to the reading taken before. And that event already carried code 20
and `face_id` 1969491410946 *before* anyone named that person, while the six
detections around it (7:40, 7:44, 7:47, 7:52, 7:56, 8:05) are all 22 with no
face at all.

So the hub clusters faces and hands out ids on its own; the name is attached
app-side and never reaches the local record. Which is also why the faces card
takes its names from card config: there is nothing local to read them from.

An app label reading "unknown" or "stranger" on a 22 event would still settle
22 outright. Only code 10 is now unnamed, and it has never appeared without 17.

Only three are named. 6 and 22 are the commonest after motion but never appear
alone, so nothing in the data separates what they mean from what accompanies
them; naming either would be a guess printed onto every recording. 10 is
excluded from `RING_ALARM_TYPES` for the same reason — it has never been seen
without 17, so adding it would claim more than was observed.

The earlier `alarm_type` 17 on the side doorbell at 21:16 on 2026-08-12, the
one with a 30-second clip, was therefore also a press.

### Quick replies and chime settings are not on the hub

Probed 2026-08-17 with read-only getters in one session:
`getQuickResponseList`, `getQuickRespAudioList`, `getQuickResponseConfig`
(all with a `quick_response` block), `getChimeCtrlList`, `getChimeAlarmConfig`,
`getRingStatus` and `getBellConfig` -- every one `-40106`, with none of the
"exists, wrong params" replies. `getMsgPushConfig` came back as an outright
refusal rather than `-40106`, which reads as a method that exists and is not
for this session's credentials. No setter was attempted; there is nothing to
set against.

Also verified the same day, and now load-bearing: `searchVideoWithUTC` and
`searchDetectionList` ride a single `multipleRequest` with `error_code 0`
each and results byte-identical to the individual calls (18 ms for the
pair). The poll batches them per camera on the strength of it.

### There is no face library

Detections of `alarm_type` 20 carry `event_info` with a `face_id` and a
`face_bitmap`. Neither can be resolved into a person by the hub:

| Call | Result |
| --- | --- |
| `getFaceList`, `getFaceInfo`, `searchFaceList` | `-40106` |
| `getFaceLibrary`, `getFaceDetectionCapability` | `-40106` |
| `getFaceTrackingConfig` | `-40106` |
| `getFaceDetectionConfig` with `name` `face_list` / `library` | `0`, but returns the same `detection` block whatever section is asked for |

So there is no enumeration of known faces, no name and no image. And
`face_bitmap` was `0` on every face detection observed, so it is not a mask
over the `tags` list — it categorises nothing.

What is usable: **four distinct `face_id` values across five face detections**,
so one recurred. The id appears stable per person, which is enough for an
automation to match one and supply the name the hub will not. Exposed as
`face_ids` on the event entity and on each listed recording.

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

### mirrorscreen: solved

This section previously recorded the params as unknown. They are not — see
*mirrorscreen is reachable, and empty* above: `name` must be a list, the call
then returns `error_code 0`, and the component holds no configuration.

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

~~**Only two codes are named**, and only where the evidence carries: `2` is set
on nearly every detection, and `20` is the only code observed carrying a
`face_id`. The rest are real and unnamed.~~ **(superseded — see "What the
alarm_type codes mean" above.)** Nine codes are named now, each against
observed detections, and anything outside that set is still displayed as its
number rather than guessed at. The hub cannot name them either —
`getAlertTypeList` is `-40106` and `getAlertConfig` returns `{}`.

~~**Which code means a doorbell press is still unknown.** `RING_ALARM_TYPES` in
`const.py` is deliberately empty.~~ **(superseded.)** `alarm_type` 17 is a
press, confirmed against a real one, and `RING_ALARM_TYPES` is `{17}`. Code 10
rides along with it every time and has never been seen alone, so it is read as
a missed press rather than added to the set — that would claim more than was
observed.

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
  classification in the *clip index*. ~~which is the same reason a doorbell
  press cannot be told apart from motion.~~ **(superseded.)** The detection
  log carries it: `searchDetectionList` answers with an `alarm_type` per
  event, and `attach_detections` puts the two together so one record carries
  both.
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

### `subg` has never been asked

The component list advertises it and no probe has ever touched it. It is the
sub-GHz link between the hub and the TD21 doorbells, which is to say it is the
layer the failure this project exists around lives in: the cameras keep their
radio link, still answer live view, and record nothing, and nothing on the LAN
can see that link's state at all.

`tools/probe_subg.py` asks, in one login: the five section spellings that work
for `app_component` and `general_camera_manage`, ten more from a radio's own
vocabulary, and ten method names. Read-only by construction, and not by
convention -- every request is walked before it is sent, and any `method` that
is not a getter or any key that is a write verb refuses the run. `do` is
checked as a key rather than a method name, because that is how Tapo's write
verb travels, and the walk goes into lists because sub-requests arrive in one.

It has not been run. It touches a hub that stops responding under repeated
authentication, so it is somebody's decision to make and not a script's.

Any section that does not answer `-40106` would be the first LAN-visible fact
about the camera radio anybody has had. Signal strength or a last-heard time
per camera would turn "the cameras are dark" from an inference drawn from
silence into a reading -- and would say whether a dark camera has lost the
radio or is holding it and refusing to record. Those are different faults with
different cures, and today they are indistinguishable.

### What cannot be answered from the LAN at all

Three questions are left, and none of them can be settled by asking the hub.

- **A faster event source.** `eventCenter` and `ringLog` are advertised and
  every namespace and method route returns `-40106`. If the app reaches them
  it does so by a shape no amount of guessing here has found, and the way to
  learn it is a capture of the app's own traffic -- a proxy between the phone
  and the hub, with the app's certificate pinning dealt with.
- **Live view.** The session opens, authenticates, is acknowledged, and no
  video arrives. Every step this end can perform has been performed. What is
  missing is whatever the app sends that this does not, which is the same
  capture.
- **The radio itself.** If `subg` turns out to be unreachable too, what is
  left is listening to the link rather than asking about it: a software-defined
  radio at 868 or 915MHz, which is a different project with different
  equipment.

None of that is a matter of more probing. It is a matter of instruments this
repository does not have.

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
