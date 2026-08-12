#!/usr/bin/env python3
"""Hold one H500 login and serve requests over it.

pytapo's authenticate() is `if not self.stok: refresh`, so a live process logs
in once and every later request rides that session. Running one process per
experiment is what wedges the hub. This keeps a single process alive and lets
anything on the machine send requests to it.

    tools/h500_session.py --host 192.168.1.50 &        # start it
    tools/h500_session.py --send '{"method":"get","led":{"name":"config"}}'
    tools/h500_session.py --health
    tools/h500_session.py --stop

Read-only by default: every method name in a request, including ones nested in
a multipleRequest, must pass the same filter the probe uses. A long-lived
session that will run formatSdCard on request is a footgun, so writes need
--allow-writes and are still refused for the explicitly dangerous names.

Binds to 127.0.0.1 only, and shuts itself down after --idle seconds so a
forgotten daemon does not sit on a hub session indefinitely.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_live as probe  # noqa: E402

DEFAULT_PORT = 8765
state = {"client": None, "requests": 0, "last": 0.0, "allow_writes": False,
         "started": 0.0, "raw": False, "kx": None}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # the access log is noise; the response carries what matters

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {
                "ok": state["client"] is not None,
                "requests": state["requests"],
                "uptime_seconds": round(time.monotonic() - state["started"], 1),
                "idle_seconds": round(time.monotonic() - state["last"], 1),
                "allow_writes": state["allow_writes"],
                "raw": state["raw"],
            })
        elif self.path == "/stop":
            self._reply(200, {"stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._reply(404, {"error": "use POST /, GET /health, GET /stop"})

    def do_POST(self):
        state["last"] = time.monotonic()
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as err:
            return self._reply(400, {"error": f"not JSON: {err}"})
        if not isinstance(request, dict):
            return self._reply(400, {"error": "request must be an object"})

        if self.path == "/media":
            # Reuses this session's login instead of spawning another, which is
            # what exhausts the hub.
            state["requests"] += 1
            return self._reply(200, media_probe(
                int(request.get("camera", 0)), int(request.get("seconds", 5)),
                request.get("save")))

        unsafe = [name for name in probe.methods_in(request)
                  if not probe.is_safe(name)]
        if unsafe and not state["allow_writes"]:
            return self._reply(403, {
                "error": "refused: read-only session",
                "methods": unsafe,
                "hint": "restart with --allow-writes if you mean it",
            })
        if any(name in probe.NEVER_SEND for name in probe.methods_in(request)):
            return self._reply(403, {
                "error": "refused: never sent, even with --allow-writes",
                "methods": [n for n in probe.methods_in(request)
                            if n in probe.NEVER_SEND],
            })

        state["requests"] += 1
        try:
            response = state["client"]._hub.performRequest(request)
            # Scrubbed by default. Raw responses carry device IDs and MACs, and
            # probe output gets pasted into issues and chat logs.
            self._reply(200, {"response": response if state["raw"]
                              else probe.scrub(response)})
        except Exception as err:
            self._reply(200, {"error": f"{type(err).__name__}: {err}"})


def install_key_exchange_spy() -> None:
    """Record the shape of the hub's Key-Exchange header.

    NonceMissingException means the header arrived but pytapo's parser found no
    nonce in it, so the header's format is the evidence. Values are truncated —
    only the structure matters.
    """
    from pytapo.media_stream import crypto

    # Unwrap to the plain function so cls is threaded through. Capturing the
    # bound classmethod would pin cls to AESHelper and silently defeat any
    # subclass the integration installs.
    original = crypto.AESHelper.from_keyexchange_and_password.__func__

    def spy(cls, key_exchange, *args, **kwargs):
        raw = (key_exchange.decode(errors="replace")
               if isinstance(key_exchange, bytes) else str(key_exchange))
        state["kx"] = {
            "length": len(raw),
            "space_parts": len(raw.split(" ")),
            "has_nonce": "nonce" in raw,
            "empty_nonce": 'nonce=""' in raw.replace(" ", ""),
            "helper": cls.__name__,
            "structure": re.sub(r'([^",= ]{7,})', lambda m: m.group(1)[:6] + "…", raw),
        }
        return original(cls, key_exchange, *args, **kwargs)

    crypto.AESHelper.from_keyexchange_and_password = classmethod(spy)


def media_probe(camera_index: int, seconds: int,
                save: str | None = None) -> dict:
    """Replay a known-good download over the session's existing login."""
    client = state["client"]
    state["kx"] = None
    try:
        camera = client.camera_at(camera_index)
        now = int(time.time())
        clips = client.recent(camera, now - 86400, now)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}
    if not clips:
        return {"error": "no clip in the last 24h to replay"}
    clip = clips[-1]
    start = int(clip["startTime"])

    async def pull():
        received = 0
        handle = open(save, "wb") if save else None
        try:
            async for chunk in client.iter_recording(camera, start, start + seconds):
                received += len(chunk)
                if handle:
                    handle.write(chunk)
                if received > 200_000:
                    break
        finally:
            if handle:
                handle.close()
        return received

    result = {"clip": start, "video_type": clip.get("video_type")}
    try:
        result["bytes"] = asyncio.run(pull())
    except Exception as err:
        result["error"] = f"{type(err).__name__}: {err}"
    result["key_exchange"] = state.get("kx")
    return result


def call(port: int, path: str, body: str | None = None) -> str:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                url, data=body.encode() if body is not None else None,
                method="POST" if body is not None else "GET"),
            timeout=60,
        ) as reply:
            return reply.read().decode()
    except urllib.error.HTTPError as err:
        # Subclass of URLError, so it must be caught first or a refusal's
        # explanation is thrown away and reported as a missing session.
        return err.read().decode()
    except urllib.error.URLError as err:
        return json.dumps({"error": f"no session on {port}: {err}"})


def watchdog(server: HTTPServer, idle: float) -> None:
    while True:
        time.sleep(5)
        if time.monotonic() - state["last"] > idle:
            print(f"idle for {idle:.0f}s, shutting down", flush=True)
            server.shutdown()
            return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--username",
                        default=os.environ.get("TAPO_USERNAME", "admin"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--idle", type=float, default=1800,
                        help="shut down after this many idle seconds")
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--raw", action="store_true",
                        help="do not redact device IDs and MACs in responses")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--send", help="send one JSON request to a running session")
    parser.add_argument("--media", action="store_true",
                        help="replay a known-good download over the "
                             "running session")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--save", help="write the media test stream here")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()

    if args.send is not None:
        print(call(args.port, "/", args.send))
        return 0
    if args.media:
        print(call(args.port, "/media", json.dumps(
            {"camera": args.camera, "save": args.save})))
        return 0
    if args.health:
        print(call(args.port, "/health"))
        return 0
    if args.stop:
        print(call(args.port, "/stop"))
        return 0
    if not args.host:
        parser.error("--host is required to start a session")

    probe.load_env_file(Path(args.env_file))
    password = os.environ.get("TAPO_PASSWORD")
    if not password:
        parser.error("TAPO_PASSWORD not set and no .env entry for it")

    state["allow_writes"] = args.allow_writes
    state["raw"] = args.raw
    state["client"] = probe.load_api().H500Client(
        args.host, args.username, password,
        os.environ.get("TAPO_CLOUD_PASSWORD", ""), debug=args.debug)
    install_key_exchange_spy()
    state["client"].connect()
    state["started"] = state["last"] = time.monotonic()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=watchdog, args=(server, args.idle), daemon=True).start()
    print(f"session up on 127.0.0.1:{args.port} (pid {os.getpid()}), "
          f"{'writes allowed' if args.allow_writes else 'read-only'}, "
          f"idle timeout {args.idle:.0f}s", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state["client"].close()
        print(f"session closed after {state['requests']} requests", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
