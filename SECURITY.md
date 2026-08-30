# Security

## Reporting

Open a [security advisory](https://github.com/Sujeom/tapo-h500-local-recordings/security/advisories/new)
rather than an issue. If that is unavailable to you, open an issue saying only
that you have found something and asking for a private channel — no details.

## What this integration holds

**Hub credentials.** The Home Assistant username, password and TP-Link cloud
password for the hub are stored in the config entry, which lives in Home
Assistant's `.storage`. They are held because the hub requires them on every
login; they are never written anywhere else, never logged, and never sent
anywhere but the hub.

The cloud password is used only to derive the local media-encryption key. No
TP-Link account is contacted, at setup or ever.

**Recordings.** Downloaded clips, thumbnails and JSON sidecars are written
under Home Assistant's `local` media directory, with paths built from the
hub's own camera names. Those paths are checked against the media root before
anything is written, and a composed path that leaves it is refused.

## What leaves the house

Nothing. Control on `443`, media on `8800`, both to the hub's LAN address.
The integration issues no request to TP-Link, no telemetry, and no update
check — see [README](README.md#local-only-by-design). Blocking the hub and
cameras from the WAN is a supported configuration and the one this is built
for.

## Diagnostics

The diagnostics download is built from an allow-list rather than a
deny-list: a field has to be added deliberately to appear in it. It carries
no credentials, no MAC addresses, no camera aliases, no cloud account and no
wall-clock times — ages are relative. That is asserted by generating the file
from a hub carrying all of those and checking none of them appear.

## The HTTP endpoint

The integration serves one view, for clip preview frames. It requires
authentication, and the URLs the dashboard uses are signed and expiring.
Identifiers are validated twice before becoming a path: the shape is refused
first, and the resolved path is then checked against the media root.

## Supported versions

The latest release. This is a single-maintainer project reverse engineered
against one firmware; there is no back-porting.
