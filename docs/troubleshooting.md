# When something stops working

Organised by what you see, not by what the code calls it. Most of these have
an entity that already knows the answer — the trick is knowing which one to
look at.

If you are about to open an issue, get a diagnostics download first: **Settings
→ Devices & services → Tapo H500 → the three dots → Download diagnostics**. It
is built from an allow-list, so it carries no credentials, no MAC addresses,
no camera names and no wall-clock times; ages are relative. Attaching it
usually saves a round trip.

## The integration will not set up

Two different failures that look the same from outside.

**"The H500 refused the stored credentials."** The hub said no to a login. It
will not retry, because retrying is not free: this hub wedges under repeated
authentication and recovers only on a timeout, so hammering it makes things
worse. Home Assistant asks you to re-enter the password instead.

The usual cause is the username. It must be `admin` — the camera account —
**not** your TP-Link email. The hub refuses the email with an error that is
indistinguishable from a lockout.

**"Cannot reach the H500 at …"** Anything else: no route, no answer, a reset
mid-handshake, a garbage reply. Home Assistant retries this on a backoff by
itself, and you do not have to do anything. If it never comes back, the hub is
probably wedged — see below.

The two are told apart by whether the hub named a refusal code. A dropped
connection carries no code, so it is never treated as a wrong password. That
asymmetry is deliberate: telling somebody their password is wrong when the hub
is merely busy sends them to change a password that was fine.

## Everything went unavailable at once

The hub's media port has wedged. It accepts a connection and then closes it
before sending a byte — the port is open, the handshake never comes.

- `sensor.<hub>_media_sessions` counts how the recent sessions went.
- `sensor.<hub>_media_healthy_for` is how long it has been fine. It reads zero
  while a wedge is happening and climbs once it is over, so the recorder keeps
  a usable shape of it.
- The repair notice **"The H500 has stopped serving recordings"** appears
  when it has been going on.

**It recovers on a timeout, not on a retry.** Anything that pokes it again
resets the clock. Leave it alone for a few minutes. Restarting Home Assistant
does not help and costs another login; power-cycling the hub does, and the
integration will offer to do that for you if automatic restart is enabled.

## A camera has gone quiet

`binary_sensor.<camera>_silent` turns on when a camera that normally records
has not recorded for longer than the silence window, which is set on the
Configure page. `binary_sensor.<hub>_cameras_not_recording` is the same
question asked of all of them at once.

**Check the age of the newest clip before assuming the downloads broke.** The
usual cause is a hub reboot: a TD21 can survive one in the paired list while
having stopped sending anything, so the camera looks present and is not. The
paired list will not tell you — `sensor.<camera>_last_activity` will.

Re-pair the camera in the Tapo app if it stays quiet. There is nothing this
integration can do from here; the hub is the only thing that talks to it.

## Downloads are failing

Two different failures again, and the counters tell them apart.

**Empty recordings.** The hub lists a clip, answers the whole media session,
and sends no video. That is one clip it cannot produce rather than a broken
pipeline, so it is remembered and not retried forever.

**Stalled sessions.** The stream stops mid-transfer. This is the wedge, or
something close to it.

`binary_sensor.<hub>_recording_service_problem` turns on when the pipeline
itself is failing, and the repair notice **"Recordings from … are not
downloading"** names the camera. `sensor.<hub>_media_sessions` carries the counts as attributes.

A clip that downloads but does not decode is deleted immediately and fetched
again while the hub still has the original — a truncated file looks identical
to a good one on disk, and by the time anybody notices, retention has evicted
the source.

## Recordings are filed under the wrong day

The hub's clock has drifted. `sensor.<hub>_clock_offset` shows by how much.

If you have blocked the hub from the internet — which is the point of this
integration — **leave NTP (UDP 123) open**. A hub that cannot set its clock
drifts, and clips get filed under the wrong date because the folder name comes
from the hub's own timestamps.

## Storage keeps filling up

`sensor.<hub>_storage_used` is the percentage; `sensor.<hub>_storage_full_in`
forecasts when it runs out, and says "measuring" rather than guessing until it
has enough history. Its attributes carry the measured rate and how many samples
it is based on, which is how you tell "not filling" from "not enough history
yet" — both show no forecast.

The repair notice **"H500 storage is … full"** appears before it matters. The hub
loop-records, so nothing breaks when it fills; the oldest recordings simply go.
Downloads keep whatever you asked to keep, which is what retention is for.

## The card says "Custom element doesn't exist"

The dashboard resource did not register. The integration adds it
automatically, but Lovelace's storage layout differs between Home Assistant
versions and it does not always take — the log carries a warning saying so
when that happens.

Add it by hand: **Settings → Dashboards → three-dot menu → Resources → Add
resource**, URL `/tapo_h500_static/tapo-h500-card.js`, type **JavaScript
Module**. Then reload the page.

If it worked before and stopped after an upgrade, this is the first place to
look — it is the integration's only dependency on another integration's
internals.

## Two cameras share a folder

The repair notice **"Two cameras share a name"**. Downloads are filed under a
slug
of the camera's own name, so two cameras called the same thing write to the
same directory and one camera's recording answers "already downloaded" for the
other. Two hubs makes this likely rather than theoretical.

Rename one in the Tapo app. Putting the hub into the path would fix it and
orphan every recording anyone has already downloaded, to solve a case most
installations do not have.

## There is no live view

There never will be. See [limitations.md](limitations.md) — it is the hardware,
not the integration.

## Nothing here matches

Turn on debug logging: **Settings → Devices & services → Tapo H500 → Enable
debug logging**, reproduce, then disable it again. That raises the hub
library's own logging as well as this integration's, which is where the
session handshake failures show up.

Then open an issue with the log and the diagnostics download.
