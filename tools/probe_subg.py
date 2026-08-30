#!/usr/bin/env python3
"""Ask the hub about `subg`, the radio the cameras actually talk over.

`subg` is advertised in `app_component_list` and has never been probed. It is
the sub-GHz link between the hub and the TD21 doorbells -- which is to say it
is the layer the wedge lives in: the cameras keep their radio link and answer
live view and record nothing, and nothing on the LAN can currently see that
link's state at all.

Read-only, by construction:

  * only `get`-shaped requests are ever sent; anything else is refused here
    before it reaches the socket, by the same list `probe_live.py` uses;
  * one login for the whole run, because this hub stops responding under
    repeated authentication and recovers only on a timeout;
  * every request is one batched `multipleRequest`, so the whole sweep is a
    handful of round trips rather than one per guess;
  * nothing is retried. A hub that has stopped answering is a hub to leave
    alone, and the run stops on the first transport failure.

    python3 tools/probe_subg.py --host 192.168.11.5

Passwords come from TAPO_PASSWORD / TAPO_CLOUD_PASSWORD or an interactive
prompt; nothing is read from the command line.

What an answer would mean
-------------------------
Any section that does not return -40106 is the first LAN-visible fact about
the camera radio anybody has had. Signal strength or a last-heard timestamp
per camera would turn "the cameras are dark" from an inference drawn from
silence into a reading -- and would say whether a dark camera has lost the
radio or is holding it and refusing to record, which are different faults with
different cures and are indistinguishable today.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The namespace, and every section spelling the working namespaces use. The
# same five that `app_component` and `general_camera_manage` answer to, plus
# the ones a radio link would plausibly file its state under.
probe_live = None

NAMESPACE = "subg"
SECTIONS = (
    # The five that work elsewhere on this hub.
    "config", "info", "status", "list", "subg",
    # A radio's own vocabulary.
    "rf", "radio", "link", "pair", "paired_list", "device_list",
    "signal", "rssi", "channel", "network",
)

# Method names worth one try each, in the shape pytapo uses. The sweep above
# is the namespace route; this is the method route, and the two have found
# different things before.
METHODS = (
    "getSubgConfig", "getSubgStatus", "getSubgInfo", "getSubgList",
    "getRfConfig", "getRfStatus", "getSubgPairList", "getSubgDeviceList",
    "getSubgSignal", "getSubgChannel",
)

def _error_code(reply):
    """The hub's code for a reply, under either spelling it uses.

    `performRequest` hands back `err_code` on a rejected envelope and
    `error_code` inside an evaluated one, and reading only the second is how
    a run of rejected envelopes looked like a run of answers.
    """
    if not isinstance(reply, dict):
        return None
    inner = probe_live.find_error_code(reply) if probe_live else None
    if inner is not None:
        return inner
    for key in ("error_code", "err_code"):
        if key in reply:
            return reply[key]
    return None


NOT_A_METHOD = -40106
# The envelope was rejected and the method never evaluated: an answer about
# the request shape, not about the method.
ENVELOPE_REJECTED = 40210


# Tapo's write verbs. `do` is the counterpart of `get` and travels as a KEY
# under a namespace rather than as a method name, so checking `method` alone
# lets `{"method": "get", "subg": {"do": {...}}}` straight through.
WRITE_VERBS = frozenset({"do", "set", "add", "delete", "remove", "edit"})

# The batching envelope. It carries other methods and performs nothing itself,
# so it is allowed by name -- but only as a carrier: everything inside it is
# still walked, and one setter in the list refuses the whole request.
CARRIERS = frozenset({"multipleRequest"})


def _safe(request) -> bool:
    """Only reads. A radio's setters could unpair a camera.

    Every `method` anywhere must be a getter, and no key anywhere may be a
    write verb -- a batched request carries arbitrary sub-requests, and a
    namespace carries arbitrary sections, so both routes have to be walked.
    """
    def walk(node):
        if isinstance(node, dict):
            method = node.get("method")
            if (isinstance(method, str) and not method.startswith("get")
                    and method not in CARRIERS):
                return False
            if any(str(key).lower() in WRITE_VERBS for key in node):
                return False
            return all(walk(value) for value in node.values())
        if isinstance(node, list):
            return all(walk(item) for item in node)
        return True

    return walk(request)


def _requests():
    """Every namespace probe, as one list of `get` requests.

    The direct form, which is what `app_component` and
    `general_camera_manage` answer to on this hub.
    """
    for section in SECTIONS:
        yield (f"{NAMESPACE}.{section}",
               {"method": "get", NAMESPACE: {"name": [section]}})


def _method_names() -> tuple[str, ...]:
    """Methods to try through `executeFunction`, not as raw envelopes.

    Sending `{"method": name, "params": {}}` down `performRequest` comes back
    40210 whatever the name is: the envelope is rejected before the hub looks
    at the method, so the reply says nothing about whether it exists. That
    trap is already written up in the protocol notes, and it caught this
    probe too. `executeFunction` builds the envelope the hub evaluates.
    """
    return METHODS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--env-file", default=".env",
                        help="KEY=value credentials, parsed not sourced")
    parser.add_argument("--json", action="store_true",
                        help="print every raw reply rather than a summary")
    args = parser.parse_args()

    # Parsed, never sourced: handing an unquoted value to a shell runs
    # anything after a space and prints it in the error.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "probe_live", str(Path(__file__).with_name("probe_live.py")))
    global probe_live
    probe_live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe_live)
    probe_live.load_env_file(Path(args.env_file))
    password = os.environ.get("TAPO_PASSWORD") or getpass.getpass(
        "Camera account password: ")
    cloud = os.environ.get("TAPO_CLOUD_PASSWORD") or getpass.getpass(
        "TP-Link cloud password: ")

    # Through probe_live's loader: importing the package body pulls in Home
    # Assistant, which is not installed beside pytapo in the probe venv.
    client = probe_live.load_api().H500Client(
        args.host, args.username, password, cloud)
    answered, rejected = {}, []
    def record(label, reply):
        code = _error_code(reply)
        if code == NOT_A_METHOD:
            return True
        if code == ENVELOPE_REJECTED:
            # Never evaluated, so it says nothing about whether the method
            # exists. Reporting it as a hit would be a lie.
            rejected.append(label)
            return True
        answered[label] = reply
        if args.json:
            print(label, json.dumps(reply, indent=2))
        return True

    try:
        client.connect()
        for label, request in _requests():
            if not _safe(request):
                print(f"refusing to send {label}: not a get", file=sys.stderr)
                return 2
            try:
                record(label, client._hub.performRequest(request))
            except Exception as err:
                if str(NOT_A_METHOD) in str(err):
                    continue
                print(f"stopping at {label}: {type(err).__name__}: {err}",
                      file=sys.stderr)
                break
        for method in _method_names():
            if not _safe({"method": method, "params": {}}):
                print(f"refusing to send {method}: not a get", file=sys.stderr)
                return 2
            try:
                record(method, client._hub.executeFunction(method, {}))
            except Exception as err:
                if str(NOT_A_METHOD) in str(err) or "result" in str(err):
                    continue
                print(f"stopping at {method}: {type(err).__name__}: {err}",
                      file=sys.stderr)
                break
    finally:
        client.close()

    if rejected:
        print(f"{len(rejected)} probes were never evaluated "
              f"({ENVELOPE_REJECTED}, envelope rejected): "
              f"{', '.join(rejected)}", file=sys.stderr)
    if not answered:
        print(f"{NAMESPACE}: every section and method answered "
              f"{NOT_A_METHOD}. Advertised and unreachable, like "
              f"playbackDelete and ringLog before it.")
        return 1
    print(f"{NAMESPACE}: {len(answered)} of "
          f"{len(SECTIONS) + len(METHODS)} answered")
    for label, reply in answered.items():
        print(f"  {label}: {json.dumps(reply)[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
