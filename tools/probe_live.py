#!/usr/bin/env python3
"""Find the H500's live-view verb by asking the hub instead of sniffing the app.

Phase A is free and touches nothing: it dumps the paired camera record and the
child component list.

Phase B (--probe) opens real media sessions on port 8800. It always runs a
known-good `type=download` session first as a control: if the control returns
JSON, the harness and credentials work and any failure afterwards is specific to
the verb being tried. Without that control an HTTP 401 is unreadable.

Port 8800 has been observed refusing connections after a rejected session, so
the run aborts the moment it sees ConnectionRefused rather than reporting three
more meaningless failures.

    python3 tools/probe_live.py --host 192.168.1.50 --camera 1
    python3 tools/probe_live.py --host 192.168.1.50 --camera 1 --check
    python3 tools/probe_live.py --host 192.168.1.50 --camera 1 --probe

Passwords come from TAPO_PASSWORD / TAPO_CLOUD_PASSWORD or an interactive
prompt; nothing is read from the command line.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import json
import os
import re
import socket
import sys
import time
import types
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "tapo_h500"
MEDIA_PORT = 8800

try:
    from pytapo.const import ERROR_CODES
except ImportError:
    ERROR_CODES = {}

api = None


def load_api():
    """Import the component's api module without running its HA __init__.

    Deferred so the pure helpers below stay testable without pytapo installed.
    """
    global api
    if api is None:
        package = types.ModuleType("tapo_h500")
        package.__path__ = [str(COMPONENT)]
        sys.modules.setdefault("tapo_h500", package)
        try:
            api = importlib.import_module("tapo_h500.api")
        except ModuleNotFoundError as err:
            if err.name and err.name.split(".")[0] != "pytapo":
                raise
            raise SystemExit(
                "pytapo is required to talk to the hub. In this repo:\n"
                "  python3 -m venv .venv && .venv/bin/pip install pytapo==3.4.18\n"
                "  .venv/bin/python tools/probe_live.py --host <ip> --camera 1"
            ) from err
    return api


# Query "type" and payload block name are the same word in the verified
# download path, so each candidate varies both together.
CANDIDATES = ("preview", "live", "stream", "video")

# Device IDs, parent IDs and MACs are all long hex strings. Matching the shape
# rather than the key name means a field nobody anticipated is still redacted.
HEX_ID = re.compile(r"^[0-9A-Fa-f]{12,}$")


def scrub(value):
    if isinstance(value, str):
        return f"{value[:6]}…" if HEX_ID.match(value) else value
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def query_for(camera, block: str, player_id: str, media_type: bool) -> dict:
    """The verified download query string with only "type" changed."""
    query = {
        "deviceId": camera["device_id"],
        "type": block,
        "playerId": player_id,
    }
    if media_type:
        query["media_type"] = 0
    return query


def build_payload(block: str, camera: dict, player_id: str, client_id: int) -> dict:
    """The verified download payload's identity fields, with a live verb."""
    return {
        "type": "request",
        "seq": 1,
        "params": {"method": "get", block: {
            "dev_id": camera["device_id"],
            "mac": camera["mac"],
            "channels": [int(camera.get("channel_id", 0))],
            "client_id": client_id,
            "player_id": player_id,
            "audio": ["default"],
            "resolutions": ["HD"],
        }},
    }


def classify(mimetype: str, plaintext: bytes) -> tuple[str, object]:
    """Turn one media-session response into a verdict."""
    base = mimetype.split(";", 1)[0].strip()
    if base == "video/mp2t":
        return "video", len(plaintext)
    if base != "application/json":
        return "other", base
    try:
        message = json.loads(plaintext.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "other", base
    code = message.get("params", {}).get("error_code", 0)
    if code:
        return "error", {"code": code, "meaning": ERROR_CODES.get(str(code))}
    return "json", message


def tcp_alive(host: str, timeout: float = 3.0) -> bool:
    """Is the media listener accepting connections at all?"""
    with socket.socket() as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, MEDIA_PORT))
            return True
        except OSError:
            return False


async def attempt(client, query: dict, payload: dict, timeout: float
                  ) -> tuple[str, object]:
    session = load_api().H500MediaSession(
        ip=client.host, cloud_password=client.cloud_password,
        super_secret_key=client._super_secret_key,
        encryptionMethod=client._encryption_method, port=MEDIA_PORT,
        username=client.username, window_size=25, query_params=query,
    )
    async with session:
        stream = session.transceive(
            json.dumps(payload, separators=(",", ":")), no_data_timeout=timeout)
        while True:
            try:
                response = await asyncio.wait_for(stream.__anext__(), timeout)
            except StopAsyncIteration:
                return "closed", "hub ended the session"
            except asyncio.TimeoutError:
                return "timeout", f"no response in {timeout:.0f}s"
            verdict, detail = classify(response.mimetype, response.plaintext)
            if verdict != "other":
                return verdict, detail


def run_attempt(client, query, payload, timeout) -> tuple[str, object]:
    """Bound the whole attempt, not just the read, and surface partial bytes."""
    try:
        return asyncio.run(asyncio.wait_for(
            attempt(client, query, payload, timeout), timeout * 3))
    except Exception as err:
        detail = f"{type(err).__name__}: {err}"
        partial = getattr(err, "partial", None)
        if partial:
            detail += f"\n    bytes={partial!r}\n    hex={partial.hex()}"
        return "exception", detail


def control(client, camera, timeout: float, media_type: bool) -> str:
    """Replay a known-good download so a later failure is interpretable."""
    now = int(time.time())
    try:
        clips = client.recent(camera, now - 86400, now)
    except Exception as err:
        print(f"  control    skipped   could not list recent clips: {err}")
        return "skipped"
    if not clips:
        print("  control    skipped   no clip in the last 24h to replay")
        return "skipped"
    clip = clips[-1]
    start = int(clip["startTime"])
    end = min(int(clip["endTime"]), start + 5)
    query = query_for(camera, "download", client.player_id, media_type)
    payload = load_api().build_download_payload(
        camera, start, end, client.player_id, client._client_id)
    verdict, detail = run_attempt(client, query, payload, timeout)
    print(f"  control    {verdict:<9} {detail}")
    return verdict


def survey(client, index: int, raw: bool) -> dict:
    cameras = client.cameras()
    print(f"\nPaired cameras ({len(cameras)}):")
    for position, camera in enumerate(cameras):
        marker = "→" if position == index else " "
        print(f" {marker} [{position}] {camera.get('alias')} "
              f"({camera.get('device_model')})")
    camera = cameras[index]

    print(f"\nCamera record for index {index}:")
    print(json.dumps(camera if raw else scrub(camera), indent=2, sort_keys=True))

    print("\nChild component list (what the hub admits this camera can do):")
    try:
        components = client._hub.getChildDeviceComponentList()
        print(json.dumps(components if raw else scrub(components), indent=2))
    except Exception as err:
        print(f"  unavailable: {err}")

    print("\nWake-up config (battery cameras sleep; live view may need a wake):")
    try:
        print(json.dumps(client._hub.getWakeUpConfig(), indent=2))
    except Exception as err:
        print(f"  unavailable: {err}")
    return camera


def self_test() -> None:
    assert classify("video/mp2t", b"1234") == ("video", 4)
    assert classify("application/json;charset=utf-8",
                    b'{"params":{"error_code":-40106}}')[0] == "error"
    assert classify("application/json", b'{"params":{}}')[0] == "json"
    assert classify("text/plain", b"x")[0] == "other"
    assert classify("application/json", b"not json")[0] == "other"
    payload = build_payload("live", {"device_id": "c", "mac": "m"}, "p", 1)
    assert payload["params"]["live"]["dev_id"] == "c"
    assert payload["params"]["live"]["channels"] == [0]
    query = query_for({"device_id": "c"}, "live", "p", True)
    assert query == {"deviceId": "c", "type": "live", "playerId": "p",
                     "media_type": 0}
    assert "media_type" not in query_for({"device_id": "c"}, "live", "p", False)
    scrubbed = scrub({"parent_device_id": "802D536CBBE02CCC", "alias": "Side Door",
                      "nested": [{"mac": "186945AABBCC"}], "ai_enhance": 30})
    assert scrubbed["parent_device_id"] == "802D53…"
    assert scrubbed["nested"][0]["mac"] == "186945…"
    assert scrubbed["alias"] == "Side Door" and scrubbed["ai_enhance"] == 30
    print("self-test ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--check", action="store_true",
                        help="only report whether port 8800 is accepting")
    parser.add_argument("--probe", action="store_true",
                        help="open media sessions; wakes the camera")
    parser.add_argument("--only", choices=CANDIDATES,
                        help="probe a single verb instead of all of them")
    parser.add_argument("--skip-control", action="store_true",
                        help="do not replay a known-good download first")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--pause", type=float, default=5.0,
                        help="seconds between attempts")
    parser.add_argument("--no-media-type", action="store_true",
                        help="drop media_type=0 from the query string")
    parser.add_argument("--raw", action="store_true",
                        help="print device IDs and MACs unredacted")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.host:
        parser.error("--host is required")

    if args.check:
        alive = tcp_alive(args.host)
        print(f"port {MEDIA_PORT} on {args.host}: "
              f"{'accepting' if alive else 'REFUSING'}")
        return 0 if alive else 1

    password = os.environ.get("TAPO_PASSWORD") or getpass.getpass(
        "Camera account password: ")
    cloud_password = os.environ.get("TAPO_CLOUD_PASSWORD") or getpass.getpass(
        "TP-Link cloud password: ")

    client = load_api().H500Client(
        args.host, args.username, password, cloud_password)
    client.connect()
    print(f"Connected to {args.host}; client_id={client._client_id}")
    try:
        camera = survey(client, args.camera, args.raw)

        if not args.probe:
            print("\nPhase A only. Re-run with --probe to try live verbs.")
            return 0

        media_type = not args.no_media_type
        print(f"\nport {MEDIA_PORT}: "
              f"{'accepting' if tcp_alive(args.host) else 'REFUSING'}")
        print("\nProbing. This wakes the camera.")

        if not args.skip_control:
            if control(client, camera, args.timeout, media_type) == "exception":
                print("\nThe known-good download also failed, so this is the "
                      "harness or the hub, not the verb. Nothing below would "
                      "mean anything; stopping.")
                return 1
            time.sleep(args.pause)

        for block in ([args.only] if args.only else CANDIDATES):
            printable = query_for(camera, block, client.player_id, media_type)
            print(f"  query {printable if args.raw else scrub(printable)}")
            payload = build_payload(
                block, camera, client.player_id, client._client_id)
            verdict, detail = run_attempt(client, query_for(
                camera, block, client.player_id, media_type), payload,
                args.timeout)
            print(f"  type={block:<8} {verdict:<9} {detail}\n")
            if verdict == "video":
                print(f"Live video from type={block}. "
                      f"Payload block '{block}' is the verb.")
                return 0
            if "ConnectionRefused" in str(detail):
                print(f"Port {MEDIA_PORT} stopped accepting connections. Every "
                      "later verb would fail the same way for the same reason, "
                      "so stopping here. Re-check with --check before probing "
                      "again.")
                return 1
            time.sleep(args.pause)
        print("No candidate returned video. Send the bytes and error codes "
              "above; a parameter complaint means that verb is right.")
    finally:
        client.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
