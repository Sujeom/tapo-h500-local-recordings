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

NOT_A_METHOD = -40106


# Tapo's write verbs. `do` is the counterpart of `get` and travels as a KEY
# under a namespace rather than as a method name, so checking `method` alone
# lets `{"method": "get", "subg": {"do": {...}}}` straight through.
WRITE_VERBS = frozenset({"do", "set", "add", "delete", "remove", "edit"})


def _safe(request) -> bool:
    """Only reads. A radio's setters could unpair a camera.

    Every `method` anywhere must be a getter, and no key anywhere may be a
    write verb -- a batched request carries arbitrary sub-requests, and a
    namespace carries arbitrary sections, so both routes have to be walked.
    """
    def walk(node):
        if isinstance(node, dict):
            method = node.get("method")
            if isinstance(method, str) and not method.startswith("get"):
                return False
            if any(str(key).lower() in WRITE_VERBS for key in node):
                return False
            return all(walk(value) for value in node.values())
        if isinstance(node, list):
            return all(walk(item) for item in node)
        return True

    return walk(request)


def _requests():
    """Every probe, as one list of `get` requests."""
    for section in SECTIONS:
        yield (f"{NAMESPACE}.{section}",
               {"method": "get", NAMESPACE: {"name": [section]}})
    for method in METHODS:
        yield (method, {"method": method, "params": {}})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--json", action="store_true",
                        help="print every raw reply rather than a summary")
    args = parser.parse_args()

    password = os.environ.get("TAPO_PASSWORD") or getpass.getpass(
        "Camera account password: ")
    cloud = os.environ.get("TAPO_CLOUD_PASSWORD") or getpass.getpass(
        "TP-Link cloud password: ")

    from custom_components.tapo_h500.api import H500Client

    client = H500Client(args.host, args.username, password, cloud)
    answered = {}
    try:
        client.connect()
        for label, request in _requests():
            if not _safe(request):
                print(f"refusing to send {label}: not a get", file=sys.stderr)
                return 2
            try:
                reply = client._hub.performRequest(request)
            except Exception as err:
                text = str(err)
                if str(NOT_A_METHOD) in text:
                    continue
                # A transport failure means the hub has had enough.
                print(f"stopping at {label}: {type(err).__name__}: {err}",
                      file=sys.stderr)
                break
            answered[label] = reply
            if args.json:
                print(label, json.dumps(reply, indent=2))
    finally:
        client.close()

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
