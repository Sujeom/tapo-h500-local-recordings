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


def load_env_file(path: Path) -> list[str]:
    """Read KEY=value pairs without letting a shell interpret them.

    Sourcing a .env with `. ./.env` hands unquoted values to bash, which will
    execute anything after a space and print it in the error. Parsing here
    keeps secrets out of shell word-splitting entirely.
    """
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)  # a real env var still wins
        loaded.append(key)
    return loaded


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


# In the verified download path the query "type" and the payload block name are
# the same word, so earlier runs varied both together. pytapo's own hub-child
# code path says live does not work that way: for a camera behind a hub it opens
# the session with query type=video and then sends a "preview" block. Pairing
# the two by name can never produce that combination, which is why every live
# attempt so far has been inconclusive rather than negative.
#
# pytapo, Tapo.getMediaSession(StreamType.Stream) with childID set:
#     {"deviceId": childID, "playerId": playerID, "type": "video"}   # no media_type
# pytapo, Streamer._build_preview_payload():
#     {"type":"request","seq":1,"params":{"method":"get","preview":{
#      "audio":["default"],"channels":[0],"resolutions":["HD"],"deviceId":...}}}
#
# Two identity conventions are plausible for the block, so both are tried. The
# H500 addresses a child *inline* with dev_id+mac (that is the framing its
# verified download uses), whereas pytapo lets the hub resolve a childID and
# sends camelCase deviceId. The inline form is tried first because it is the
# one this hub is known to accept elsewhere.
#
# Confirmed on firmware 1.3.20: query type=video carrying a preview block is
# accepted, error_code 0, and the hub allocates a session_id. Both identity
# conventions were accepted, so the hub does not care which is used.
#
# Only query type=video appears here on purpose. type=preview returned
# "HTTP ERROR 401" and left port 8800 refusing TCP immediately afterwards, so
# the other spellings are not merely wrong, they cost a wedged hub to retry.
# The query type is what the HTTP layer authenticates; the block name is what
# the accepted session is then asked for.
#
#          label              query type   block      identity  media_type
ATTEMPTS = (
    ("video-preview-h500",    "video",     "preview", "h500",   False),
    ("video-preview-pytapo",  "video",     "preview", "pytapo", False),
    # Long shot kept only because it cannot 401: the block name the earlier
    # same-name pairing would have used.
    ("video-video-h500",      "video",     "video",   "h500",   True),
)
CANDIDATES = tuple(attempt[0] for attempt in ATTEMPTS)

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


def query_for(camera, qtype: str, player_id: str, media_type: bool) -> dict:
    """The verified download query string with only "type" changed."""
    query = {
        "deviceId": camera["device_id"],
        "type": qtype,
        "playerId": player_id,
    }
    if media_type:
        query["media_type"] = 0
    return query


def build_payload(block: str, camera: dict, player_id: str, client_id: int,
                  identity: str = "h500") -> dict:
    """A live-verb payload in one of the two plausible identity conventions.

    "h500" repeats the identity fields of the verified download payload, which
    address a child inline. "pytapo" is what pytapo's Streamer sends, where the
    hub resolves a childID instead, so there is no mac and the key is camelCase.
    """
    if identity == "pytapo":
        fields = {"deviceId": camera["device_id"]}
    else:
        fields = {
            "dev_id": camera["device_id"],
            "mac": camera["mac"],
            "client_id": client_id,
            "player_id": player_id,
        }
    return {
        "type": "request",
        "seq": 1,
        "params": {"method": "get", block: {
            **fields,
            "channels": [int(camera.get("channel_id", 0))],
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


# python-kasa's envelope for reaching a device behind the hub. The hub and the
# camera have different method tables, so a -40106 from the hub says nothing
# about whether the camera itself supports the method.
CHILD_ENUMERATION = (
    ("getComponentList", {}),
    ("getDeviceInfo", {"device_info": {"name": ["basic_info"]}}),
    ("getWakeUpConfig", {"wake_up": {"name": "config"}}),
    ("getBatteryInfo", {"battery": {"name": "info"}}),
    ("getStreamInfo", {"stream": {"name": "info"}}),
)


def child_call(client, camera: dict, method: str, params: dict):
    """Send one method to the camera itself, through the hub."""
    return client._hub.executeFunction("controlChild", {
        "childControl": {
            "device_id": camera["device_id"],
            "request_data": {"method": method, "params": params},
        }
    })


def child_discovery(client, camera: dict, raw: bool, calls) -> None:
    print("\nAsking the camera directly (controlChild passthrough):")
    for method, params in calls:
        try:
            result = child_call(client, camera, method, params)
        except Exception as err:
            print(f"  {method}: {err}")
            continue
        print(f"  {method}:")
        print(json.dumps(result if raw else scrub(result), indent=4))


# -40106 means the hub has no such method. Any other outcome means it does, so
# the control channel is an oracle for which method names exist.
ABSENT = ("-40106", "UNSUPPORTED_METHOD")
ABSENT_CODES = ("-40106",)
# Control-channel calls are cheap; --pause is for media attempts, not these.
METHOD_PAUSE = 0.2

# Never send anything that could act. pytapo's inventory contains formatSdCard,
# setReboot, deletePreset and play_alarm; called blind with empty params some
# of those would do exactly what they say.
MUTATING = re.compile(
    r"^(set|del|add|modify|format|reboot|play|reverse|manual|motor|cruise|"
    r"test|clear|reset|remove|start|stop|scan)", re.IGNORECASE)
NEVER_SEND = frozenset({
    "device_reboot", "rebootDevice", "set_led_off", "setReboot", "formatSdCard",
    "do",  # Tapo's write verb, the counterpart of "get"
})


def methods_in(request) -> list[str]:
    """Every method name anywhere in a request, including nested ones.

    A multipleRequest can carry arbitrary sub-methods, so checking only the
    outer name would let a write through inside a read-shaped envelope.
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("method"), str):
                found.append(node["method"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(request)
    return found

# Read-shaped names from pytapo's inventory that touch battery, media, channels
# or ring state, plus names the app plausibly uses that pytapo has never needed.
KNOWN_METHODS = (
    "getWakeUpConfig", "getBatteryStatus", "getBatteryPowerSave",
    "getBatteryOperatingMode", "getBatteryOperatingModeParam",
    "getChargingMode", "getPowerMode", "getBatteryCapability",
    "getAllChnInfo", "getMediaEncrypt", "getVideoCapability",
    "getVideoQualities", "getRingStatus", "getLastAlarmInfo", "getHubStorage",
    "getSdCardStatus", "getUserID", "getChimeCtrlList", "get_pair_list",
    "getDeviceIpAddress", "getAlertEventType", "getClipsConfig",
    "getRecordPlan", "getCircularRecordingConfig", "getDeviceInfo",
    "get_device_info", "searchVideoOfDay", "searchDetectionList",
)
GUESSED_METHODS = (
    "preWakeUp", "preVod", "preLive", "preDownload", "prePlayback",
    "wakeUp", "wakeUpDevice", "getPreviewInfo", "getPreviewStatus",
    "getLiveStreamInfo", "getStreamInfo", "getStreamUrl", "getVodInfo",
    "getChannelInfo", "getGeneralDeviceStatus", "getGeneralDeviceInfo",
    "getGeneralDeviceCapability", "getGeneralDeviceComponentList",
    "getComponentList", "getAppComponentList",
)


def is_safe(method: str) -> bool:
    return method not in NEVER_SEND and not MUTATING.match(method)


def find_error_code(response):
    """The hub's own code for a method, or None if we cannot find one.

    executeFunction raises KeyError('result') for unparseable replies, which
    tells us nothing about the method — so this reads the raw envelope and
    reports None rather than guessing.
    """
    try:
        return response["result"]["responses"][0]["error_code"]
    except (KeyError, IndexError, TypeError):
        pass
    if isinstance(response, dict) and "error_code" in response:
        return response["error_code"]
    return None


def classify_method(response) -> tuple[str, str]:
    code = find_error_code(response)
    if code is None:
        # Unknown is not evidence of presence. Print the reply and move on.
        return "unknown", json.dumps(response, default=str)[:160]
    if str(code) in ABSENT_CODES:
        return "absent", ""
    if code == 0:
        return "WORKS", "accepted these params"
    return "PRESENT", f"error_code={code}"


def probe_methods(client, methods, pause: float) -> dict[str, list[str]]:
    """Which method names does the hub admit to having?"""
    found: dict[str, list[str]] = {}
    for method in methods:
        if not is_safe(method):
            print(f"  {method:<34} skipped   (could mutate)")
            continue
        try:
            response = client._hub.performRequest({
                "method": "multipleRequest",
                "params": {"requests": [{"method": method, "params": {}}]},
            })
            outcome, note = classify_method(response)
        except Exception as err:
            text = str(err)
            outcome = "absent" if any(m in text for m in ABSENT) else "unknown"
            note = "" if outcome == "absent" else f"{type(err).__name__}: {text[:120]}"
        print(f"  {method:<34} {outcome:<8} {note}")
        found.setdefault(outcome, []).append(method)
        time.sleep(pause)
    return found


# Empty params get the whole envelope rejected with err_code 40210, so method
# names cannot be probed bare. Tapo params are {module: {function: ...}} and the
# module inventory is what we actually need. These are the plausible ways to ask
# for it; the first entry is the call the integration already relies on, so a
# failure there means the harness is wrong rather than the shape.
def component_shapes(camera: dict) -> tuple[tuple[str, dict], ...]:
    known_good = {
        "method": "getGeneralDeviceList",
        "params": {"general_camera_manage": {"paired_general_device_list": {}}},
    }
    app_component = {"name": "app_component_list"}
    return (
        ("CONTROL getGeneralDeviceList", {
            "method": "multipleRequest", "params": {"requests": [known_good]}}),
        ("get, module beside method", {
            "method": "get", "app_component": app_component}),
        ("get, module under params", {
            "method": "get", "params": {"app_component": app_component}}),
        ("multipleRequest[get]", {"method": "multipleRequest", "params": {
            "requests": [{"method": "get", "app_component": app_component}]}}),
        ("multipleRequest[getAppComponentList]", {
            "method": "multipleRequest", "params": {"requests": [{
                "method": "getAppComponentList",
                "params": {"app_component": app_component}}]}}),
        ("component_list", {"method": "component_list", "params": {}}),
        ("multipleRequest[getComponentList]", {
            "method": "multipleRequest", "params": {"requests": [{
                "method": "getComponentList",
                "params": {"component": {"name": "component_list"}}}]}}),
    )


INTERESTING = ("wake", "live", "preview", "stream", "vod", "play", "video",
               "record", "media", "ring", "channel", "battery", "camera")


def app_components(client) -> list[dict]:
    """The hub's own module inventory. This is the map everything else needs."""
    response = client._hub.performRequest(
        {"method": "get", "app_component": {"name": "app_component_list"}})
    return response["app_component"]["app_component_list"]


def show_components(client) -> None:
    modules = app_components(client)
    print(f"\nHub modules ({len(modules)}):")
    for module in sorted(modules, key=lambda item: item["name"].lower()):
        print(f"  {module['name']:<34} v{module.get('version')}")
    hits = [module["name"] for module in modules
            if any(word in module["name"].lower() for word in INTERESTING)]
    print(f"\nMedia-adjacent ({len(hits)}): {', '.join(sorted(hits))}")


def probe_shapes(client, camera: dict, raw: bool) -> None:
    print("\nRequest-shape probe (the control must succeed for the rest to "
          "mean anything):")
    for label, request in component_shapes(camera):
        try:
            response = client._hub.performRequest(request)
            text = json.dumps(response if raw else scrub(response), default=str)
        except Exception as err:
            text = f"{type(err).__name__}: {err}"
        print(f"\n  {label}\n    {text[:900]}")


def run_batch(client, path: Path, raw: bool) -> None:
    """Many requests, one login.

    pytapo's authenticate() is `if not self.stok: refresh`, so a live client
    logs in once and every later request rides the same session. Running each
    experiment as its own process is what exhausts the hub.
    """
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as err:
            print(f"  line {number}: not JSON ({err})")
            continue
        label = request.get("method", "?")
        try:
            response = client._hub.performRequest(request)
            text = json.dumps(response if raw else scrub(response), default=str)
        except Exception as err:
            text = f"{type(err).__name__}: {err}"
        print(f"\n  [{number}] {label}\n    {text[:900]}")
        time.sleep(METHOD_PAUSE)


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
    opened = None
    async with session:
        stream = session.transceive(
            json.dumps(payload, separators=(",", ":")), no_data_timeout=timeout)
        while True:
            try:
                response = await asyncio.wait_for(stream.__anext__(), timeout)
            except StopAsyncIteration:
                if opened is not None:
                    return "opened", {"session": opened,
                                      "then": "hub ended the session"}
                return "closed", "hub ended the session"
            except asyncio.TimeoutError:
                if opened is not None:
                    return "opened", {"session": opened,
                                      "then": f"no video in {timeout:.0f}s"}
                return "timeout", f"no response in {timeout:.0f}s"
            verdict, detail = classify(response.mimetype, response.plaintext)
            if verdict == "json":
                # error_code 0 with a session_id *opens* the stream; video
                # follows it. Returning here reported an accepted live session
                # as a dead one, which is how the first real hit was missed.
                # iter_recording in api.py has always looped past this point.
                opened = detail
                continue
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
    # pytapo's child-stream identity: camelCase deviceId, and no mac, no
    # client_id, no player_id for the hub to trip over.
    pytapo_block = build_payload(
        "preview", {"device_id": "c", "mac": "m"}, "p", 1, "pytapo",
    )["params"]["preview"]
    assert pytapo_block["deviceId"] == "c"
    assert not {"dev_id", "mac", "client_id", "player_id"} & set(pytapo_block)
    assert pytapo_block["resolutions"] == ["HD"]
    # The whole point of the rewrite: the query type and the payload block name
    # must be able to differ. If this ever collapses back to one word, the
    # combination pytapo actually uses becomes untestable again.
    assert any(qtype != block for _, qtype, block, _, _ in ATTEMPTS)
    assert ("video-preview-h500", "video", "preview", "h500", False) \
        == ATTEMPTS[0], "the confirmed shape must be tried first"
    assert len({a[0] for a in ATTEMPTS}) == len(ATTEMPTS), "labels must be unique"
    # type=preview returned 401 and then wedged port 8800. Any query type other
    # than video costs a wedged hub to learn nothing, so none may come back.
    assert {qtype for _, qtype, _, _, _ in ATTEMPTS} == {"video"}
    # An unreadable reply is not evidence that a method exists. The first
    # version of this oracle called everything PRESENT and reported 48/48.
    assert classify_method({"weird": 1})[0] == "unknown"
    assert classify_method({"result": {"responses": [{"error_code": -40106}]}})[0] \
        == "absent"
    assert classify_method({"result": {"responses": [{"error_code": 0}]}})[0] \
        == "WORKS"
    assert classify_method({"result": {"responses": [{"error_code": -40210}]}})[0] \
        == "PRESENT"
    assert find_error_code({"error_code": -40106}) == -40106
    assert find_error_code("not a dict") is None
    for dangerous in ("formatSdCard", "setReboot", "deletePreset", "play_alarm",
                      "device_reboot", "rebootDevice", "set_led_off",
                      "motorMove", "startScanHub", "setPrivacyMode"):
        assert not is_safe(dangerous), dangerous
    for harmless in ("getWakeUpConfig", "preWakeUp", "preVod", "searchVideoOfDay",
                     "get_pair_list", "getAllChnInfo"):
        assert is_safe(harmless), harmless
    assert all(is_safe(name) for name in KNOWN_METHODS + GUESSED_METHODS)
    # Synthetic identifiers. Never put a real device ID in a tracked file.
    scrubbed = scrub({"parent_device_id": "DEADBEEFCAFE0123", "alias": "Side Door",
                      "nested": [{"mac": "ABCDEF012345"}], "ai_enhance": 30})
    assert scrubbed["parent_device_id"] == "DEADBE…"
    assert scrubbed["nested"][0]["mac"] == "ABCDEF…"
    assert scrubbed["alias"] == "Side Door" and scrubbed["ai_enhance"] == 30
    print("self-test ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--username",
                        default=os.environ.get("TAPO_USERNAME", "admin"))
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--check", action="store_true",
                        help="only report whether port 8800 is accepting")
    parser.add_argument("--probe", action="store_true",
                        help="open media sessions; wakes the camera")
    parser.add_argument("--only", choices=CANDIDATES,
                        help="probe a single verb instead of all of them")
    parser.add_argument("--skip-control", action="store_true",
                        help="do not replay a known-good download first")
    parser.add_argument("--control-only", action="store_true",
                        help="one known-good download attempt, nothing else; "
                             "use this to test recovery")
    parser.add_argument("--child", action="store_true",
                        help="ask the camera itself via controlChild; control "
                             "channel only, does not touch port 8800")
    parser.add_argument("--child-method",
                        help="send one named method to the camera")
    parser.add_argument("--child-params", default="{}",
                        help="JSON params for --child-method")
    parser.add_argument("--batch",
                        help="file of one JSON request per line, all sent over "
                             "a single login")
    parser.add_argument("--debug", action="store_true",
                        help="print pytapo's protocol log (secrets redacted)")
    parser.add_argument("--components", action="store_true",
                        help="dump the hub's module inventory")
    parser.add_argument("--shapes", action="store_true",
                        help="find a request shape that returns the hub's "
                             "module inventory; control channel only")
    parser.add_argument("--methods", action="store_true",
                        help="ask the hub which method names exist; read-shaped "
                             "names only, control channel only")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--pause", type=float, default=5.0,
                        help="seconds between attempts")
    parser.add_argument("--no-media-type", action="store_true",
                        help="drop media_type=0 from the query string")
    parser.add_argument("--raw", action="store_true",
                        help="print device IDs and MACs unredacted")
    parser.add_argument("--env-file", default=".env",
                        help="KEY=value file for credentials (default .env)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    loaded = load_env_file(Path(args.env_file))
    if loaded:
        print(f"Loaded {', '.join(loaded)} from {args.env_file}")
    if not args.host:
        parser.error("--host is required")

    if args.check:
        alive = tcp_alive(args.host)
        print(f"port {MEDIA_PORT} on {args.host}: "
              f"{'accepting' if alive else 'REFUSING'}")
        return 0 if alive else 1

    password = os.environ.get("TAPO_PASSWORD") or getpass.getpass(
        "Camera account password: ")
    # Only the media session needs the cloud password. Everything else runs on
    # the control channel, so do not ask for a secret this run will not use.
    needs_media = args.probe or args.control_only
    cloud_password = os.environ.get("TAPO_CLOUD_PASSWORD") or (
        getpass.getpass("TP-Link cloud password: ") if needs_media else "")

    client = load_api().H500Client(
        args.host, args.username, password, cloud_password, debug=args.debug)
    client.connect()
    print(f"Connected to {args.host}; client_id={client._client_id}")
    try:
        if args.control_only:
            # Exactly one attempt, no survey: the point is to disturb the hub
            # as little as possible while checking whether auth works again.
            print(f"port {MEDIA_PORT}: "
                  f"{'accepting' if tcp_alive(args.host) else 'REFUSING'}")
            verdict = control(client, client.camera_at(args.camera),
                              args.timeout, not args.no_media_type)
            recovered = verdict in ("video", "json", "error", "opened")
            print("\nMedia auth works again." if recovered else
                  "\nStill failing. Wait longer, then power-cycle the hub.")
            return 0 if recovered else 1

        camera = survey(client, args.camera, args.raw)

        if args.batch:
            print(f"\nBatch from {args.batch}, one login for all of it:")
            run_batch(client, Path(args.batch), args.raw)
            if not (args.probe or args.components or args.shapes
                    or args.methods or args.child or args.child_method):
                return 0

        if args.components:
            show_components(client)
            if not (args.probe or args.shapes or args.methods or args.child
                    or args.child_method):
                return 0

        if args.shapes:
            probe_shapes(client, camera, args.raw)
            if not (args.probe or args.methods or args.child or args.child_method):
                return 0

        if args.methods:
            print("\nMethod-name oracle (-40106 means the hub has no such "
                  "method; anything else means it does):")
            found = probe_methods(
                client, KNOWN_METHODS + GUESSED_METHODS, METHOD_PAUSE)
            for outcome in ("WORKS", "PRESENT", "unknown", "absent"):
                names = found.get(outcome, [])
                print(f"\n{outcome} ({len(names)}): {', '.join(names) or '—'}")
            if found.get("unknown") and not found.get("absent"):
                print("\nNothing came back absent and most replies were "
                      "unreadable, so this run proves nothing. Treat the "
                      "unknown list as no evidence either way.")
            if not args.probe and not args.child and not args.child_method:
                return 0

        if args.child or args.child_method:
            child_discovery(client, camera, args.raw,
                            [(args.child_method, json.loads(args.child_params))]
                            if args.child_method else CHILD_ENUMERATION)
            if not args.probe:
                return 0

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

        attempts = [a for a in ATTEMPTS if not args.only or a[0] == args.only]
        for label, qtype, block, identity, wants_media_type in attempts:
            # --no-media-type still forces it off; otherwise each attempt keeps
            # the setting its hypothesis calls for.
            query = query_for(camera, qtype, client.player_id,
                              wants_media_type and media_type)
            print(f"  {label}")
            print(f"  query {query if args.raw else scrub(query)}")
            payload = build_payload(
                block, camera, client.player_id, client._client_id, identity)
            verdict, detail = run_attempt(client, query, payload, args.timeout)
            print(f"  type={qtype} block={block} ids={identity}  "
                  f"{verdict:<9} {detail}\n")
            if verdict == "video":
                print(f"Live video: query type={qtype}, payload block "
                      f"'{block}', {identity} identity fields.")
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
