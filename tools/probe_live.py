#!/usr/bin/env python3
"""Find the H500's live-view verb by asking the hub instead of sniffing the app.

Phase A is free and touches nothing: it dumps the paired camera record and the
child component list, which may already name the capability.

Phase B (--probe) opens real media sessions on port 8800. That wakes a battery
doorbell and costs battery per attempt, so it runs sequentially and stops at the
first candidate that returns video.

Read the error codes, not just pass/fail. A "method does not exist" style code
means the verb is wrong; a parameter-complaint code means the verb is right and
only the fields need fitting.

    python3 tools/probe_live.py --host 192.168.1.50 --camera 1
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
import sys
import types
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "tapo_h500"

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
        api = importlib.import_module("tapo_h500.api")
    return api

# Query "type" and payload block name are the same word in the verified
# download path, so each candidate varies both together.
CANDIDATES = ("preview", "live", "stream", "video")


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


async def probe(client, camera, block: str, timeout: float) -> tuple[str, object]:
    session = api.H500MediaSession(
        ip=client.host, cloud_password=client.cloud_password,
        super_secret_key=client._super_secret_key,
        encryptionMethod=client._encryption_method, port=8800,
        username=client.username, window_size=25,
        query_params={
            "deviceId": camera["device_id"],
            "type": block,
            "playerId": client.player_id,
        },
    )
    payload = build_payload(block, camera, client.player_id, client._client_id)
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


def redact(value, keep=6):
    text = str(value)
    return text if len(text) <= keep else f"{text[:keep]}…"


def survey(client, index: int, raw: bool) -> dict:
    cameras = client.cameras()
    print(f"\nPaired cameras ({len(cameras)}):")
    for position, camera in enumerate(cameras):
        marker = "→" if position == index else " "
        print(f" {marker} [{position}] {camera.get('alias')} "
              f"({camera.get('device_model')})")
    camera = cameras[index]

    print(f"\nCamera record for index {index}:")
    record = camera if raw else {
        key: (redact(value) if key in ("device_id", "mac", "oem_id") else value)
        for key, value in camera.items()
    }
    print(json.dumps(record, indent=2, sort_keys=True))

    print("\nChild component list (what the hub admits this camera can do):")
    try:
        print(json.dumps(client._hub.getChildDeviceComponentList(), indent=2))
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
    print("self-test ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--probe", action="store_true",
                        help="open media sessions; wakes the camera")
    parser.add_argument("--only", choices=CANDIDATES,
                        help="probe a single verb instead of all of them")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--raw", action="store_true",
                        help="print device IDs and MACs unredacted")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.host:
        parser.error("--host is required")

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

        print("\nProbing live verbs. This wakes the camera.")
        for block in ([args.only] if args.only else CANDIDATES):
            try:
                verdict, detail = asyncio.run(
                    probe(client, camera, block, args.timeout))
            except Exception as err:
                verdict, detail = "exception", f"{type(err).__name__}: {err}"
            print(f"  type={block:<8} {verdict:<9} {detail}")
            if verdict == "video":
                print(f"\nLive video from type={block}. "
                      f"Payload block '{block}' is the verb.")
                return 0
        print("\nNo candidate returned video. Send the error codes above; a "
              "parameter complaint means that verb is right.")
    finally:
        client.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
