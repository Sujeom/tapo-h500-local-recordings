# Scope, limitations and verification

What this integration can and cannot do, and what has actually been tested
against hardware rather than assumed. The protocol evidence behind these
conclusions is in [protocol-notes.md](protocol-notes.md).

## Scope and limitations

**No live stream yet.** The camera entities serve stills taken from the newest
recording, not live video.

The verb is no longer the unknown. On firmware `1.3.20` a port-8800 session
opened with query `type=video` carrying a `preview` payload block is accepted:
`error_code: 0`, a `session_id`, and then a real live-session notification from
the hub. **But no video ever arrives** — 90 seconds of an open, talking session
produced no frames, and every wake verb is absent locally, so a hub-attached
battery TD21 appears not to stream to a local client at all.

Nothing is wired into a camera entity until a real stream has been seen.
`protocol-notes.md` records exactly what was measured and what it rules
out.

`tools/probe_live.py` found that verb by asking the hub rather than sniffing the
Tapo app. The shape came from pytapo's hub-child code path: query `type=video`
carrying a **`preview`** payload block. The query type and the block name
differ, which is why earlier runs — which varied both together as one word —
never sent it.

Only `type=video` is ever sent now. `type=preview` returned `HTTP ERROR 401` and
left port 8800 refusing TCP, so the other spellings cost a wedged hub to learn
nothing.

Run the sweep over a **held login**; a fresh login per attempt is what wedges
the hub:

```
python3 -m venv .venv && .venv/bin/pip install pytapo==3.4.18
.venv/bin/python tools/h500_session.py --host 192.168.1.50 &
.venv/bin/python tools/h500_session.py --live --camera 1
.venv/bin/python tools/h500_session.py --stop
```

`probe_live.py` runs the same sweep standalone, plus the free Phase A survey:

```
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

**No per-clip deletion on the hub.** The hub advertises a `playbackDelete`
component, but it cannot be reached: every namespace spelling returns `-40106`,
`pytapo` has no such call to borrow a shape from, and TP-Link's own
documentation says SD/hub footage can only be removed by formatting.
`tapo_h500.delete_recording` therefore removes the *downloaded copy* in Home
Assistant. `tapo_h500.format_hub_storage` is the only hub-side deletion that
exists and it **erases every recording for every paired camera, with no undo**.

The same is true of the other advertised-but-unreachable components:
`snapshot` (so thumbnails stay ffmpeg frame extractions) and `eventCenter` /
`ringLog` (so events still come from polling the clip index). See
`protocol-notes.md` for exactly what was probed.

**No doorbell quick replies or chime settings.** The app's canned voice
responses and chime controls have no reachable hub-side surface on this
firmware: `getQuickResponseList`, `getQuickRespAudioList`,
`getQuickResponseConfig`, `getChimeCtrlList`, `getChimeAlarmConfig`,
`getRingStatus` and `getBellConfig` all answer `-40106` (probed 2026-08-17,
read-only getters only). `getMsgPushConfig` is refused outright. Like the
camera detection sensitivity, these most likely live on the camera or in the
cloud account rather than on the hub.

**Event latency.** The hub's detection log works and is what classifies each
recording, but events still come from the indexed clip list, and a clip is only
indexed once the hub has finished writing it — so an event trails the actual
doorbell press by the poll interval plus the clip length. Events themselves do not wait for the clip: the detection log is read first and is what raises the event.

**Recordings are classified, but not every code is named yet.** Every clip is
labelled `video_type` `"2"` whatever triggered it, so the clip index itself
classifies nothing. The hub's *detection log* does: on firmware `1.3.20` every
clip in a three-day window had a matching detection carrying an `alarm_type`
and an `events_1` bitmask of everything that fired at once, and one code even
carries a face ID.

Cards and the event entity show what the hub reported — `motion`,
`motion + face`, `person + doorbell (missed)` — and any code nobody has
identified as `type 31` rather than a guess. Nine codes have been seen
(2, 6, 8, 9, 10, 17, 19, 20, 22) and all nine are named, each against something
observed rather than assumed.

**Doorbell presses are identified.** `alarm_type` 17 is a press, confirmed
against a real one: the front doorbell was rung at 14:42:25 on 2026-08-13 and
that was the only event on that camera in six hours. Presses now classify as
`ring`, so the presses-only download mode works and the cards badge them.

What each code means. Eight of the nine are named, each against something
observed rather than guessed:

| Code | Meaning | How it was established |
| --- | --- | --- |
| 2 | **motion** | set on 31 of 35 detections — the base signal |
| 6 | **person** | see below |
| 8 | **vehicle** | the only code the app's "car" event added |
| 9 | **pet** | the only code the app's "dog" event added |
| 17 | **doorbell press** | a real press, the only event on that camera in six hours |
| 19 | **theft** | the camera was lifted off its mount, and this is what fired |
| 20 | **face** | all 5 of its detections carried a `face_id`; no other code ever did |
| 10 | unknown | only ever appears beside 17, so part of the doorbell signal |
| 22 | **unrecognised face** | code 20 carries a `face_id` in 6 of 6 detections, 22 in 1 of 18 — and that one exception is the only event carrying both, so the id belongs to the 20 |

Vehicle and pet fell out of three events the Tapo app had labelled, which
differ by exactly one code each:

```
"motion + person"        -> [2, 6,    22]
"person + motion + car"  -> [2, 6, 8, 22]     the car adds 8
"person + dog + motion"  -> [2, 6, 9, 22]     the dog adds 9
```

That left `{2, 6, 22}` for "motion + person" — one code more than labels. Two
things separate them: a recognised face (20) accompanies **6** in all five of
its detections but 22 only once, and a face is a person; and the confirmed
doorbell press carried **6** and not 22, and someone pressed it.

Nothing is unnamed at present, but a code that has never been seen would
display as `type 31` rather than a guess. To name a new one, note what actually
happened at a given minute and check the code on that event — a car, a
delivery, a pet — then add it to `DETECTION_NAMES` with the observation that
justifies it.

The protocol is undocumented. Pinning the H500 to a stable LAN address is
strongly recommended. Firmware changes may require integration updates.

Requires Home Assistant 2024.11 or newer.

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
  corresponding method. See `protocol-notes.md`.

The dashboard cards have their own tests, which need Node but no browser and no
Home Assistant (`node --test tests/test_cards.mjs`, 43 checks): escaping,
relative times, hour grouping, face grouping, the summary chart's scale and
labelling, and that no card ever points a `<video>` at a clip that has not been
downloaded.

Verified by unit test (`python3 -m unittest discover -s tests`, 99 tests, no
hub or Home Assistant install required):

- The H500 download request payload and the required `Content-Length: 0` outer
  framing.
- The verified 25-packet acknowledgement window and finished-notification
  handling.
- Doorbell-versus-motion classification, both timestamp field spellings, clip
  flattening, and camera-name sanitising against path traversal.
- That a quiet detection search returns nothing without disabling itself. An
  empty result is `{}` rather than `[]`, and treating that as a refusal once
  cost a whole session's detections.
- That hub status is fetched after the detection lookups and not on every poll,
  and that the camera list is cached but never left empty.
- That the slow lookups thin out while the hub is failing rather than
  thickening, and that the poll slows to six seconds after ten minutes of
  nothing happening and snaps back the moment anything does. The cost is one
  event: the first thing to happen after a quiet stretch is noticed up to six
  seconds late. A configured interval longer than six seconds is left alone.
- That an empty nonce reaches key derivation intact, a real nonce is passed
  through untouched, and the session module resolves the patched helper.

Measured against the hub on a held session, median of five calls: a detection
lookup is 19ms per camera, the clip index 17ms, the camera list 58ms, and the
14-request batched hub status 430ms. Those numbers set the poll interval.

The wedge is also a number, not only a binary sensor. "Media healthy for"
counts the hours since the media path last stopped serving, climbing while it
does and zero while it does not, so the recorder keeps it in long-term
statistics after the binary sensor's own history has been purged. The peaks
are the times to wedge, the resets are the wedges, and the attributes carry
the counts for the last day and week and the best run so far. None of it is
written to disk, so it spans this Home Assistant's uptime rather than the
hub's life.

**Not yet verified against hardware:** hub storage formatting.

**Known not to work:** there is no live view. A media session opens and is
acknowledged, but the camera never wakes and no video arrives.

**Detection types are decoded**, contrary to earlier versions of this file:
`alarm_type` 17 is a doorbell press, confirmed against a real one. All nine
observed codes are named; see `DETECTION_NAMES` in
`../custom_components/tapo_h500/const.py` for what each was named against.

Code 10 is the weakest and is marked as such. It means a press nobody answered,
which fits a doorbell that places a call, but the measurement cannot separate
that from "10 is simply part of every press" — both predict the 5 of 5 that was
observed, and none of those presses was answered in the app. Only an answered
press settles it: 17 without 10 confirms the name, while an answered press
still carrying 10 disproves it.

## Artwork

`brand/` holds the project's original icon and logo. The copies that Home
Assistant actually serves live in `custom_components/tapo_h500/brand/`.

Since Home Assistant 2026.3 a custom integration ships its own brand images
from a `brand/` directory beside `manifest.json`, served through
`/api/brands/integration/<domain>/<image>`, and local images take priority over
the CDN. Nothing is declared in `manifest.json` -- the domain determines the
path. The `custom_integrations/` folder in home-assistant/brands is now legacy
and auto-closes pull requests pointing at it, so there is nothing to submit.

Sizes are fixed and a wrong one fails silently, so `tests/test_brand.py`
asserts them: icons exactly 256x256 and 512x512; logos measured on their
shortest side, 128-256 and 256-512.

**HACS may still show a blank icon.** Its dashboard fetches from
`data-v2.hacs.xyz`, which has no entry for integrations that only ship local
brand assets. Home Assistant's own Integrations and device pages are unaffected.
See hacs/integration issues #5171 and #5223.
