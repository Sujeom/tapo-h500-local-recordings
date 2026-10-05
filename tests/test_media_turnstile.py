"""One media session at a time, with the dashboard ahead of the downloads.

The hub serves one media session at a time and wedges under more, so every
session -- a clip download, a card tile's preview, the camera entity's
picture -- goes through one lock. Taken in arrival order, a tile's preview
queued behind a dual-lens 4K camera's downloads waits for every one of them,
four to eleven minutes each. That is what "the cards take longer to load"
was. A download now gives way: it does not start while a preview is
waiting, and a preview waits only for the session already running.
"""
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402,F401  (installs the HA stubs)

api = importlib.import_module("tapo_h500.api")
CAMERA = {"device_id": "child", "mac": "AABB", "channel_id": 0}


class _StubMediaSession:
    """Replays a scripted sequence of media-session responses."""

    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def run(coro):
    return asyncio.run(coro)


class Turnstile(unittest.TestCase):
    def _session(self, gate, log, label, interactive, hold=0.01):
        async def one():
            async with gate.hold(interactive):
                log.append(f"start {label}")
                await asyncio.sleep(hold)
                log.append(f"end {label}")
        return one()

    def test_a_preview_goes_ahead_of_downloads_queued_before_it(self):
        async def scenario():
            gate, log = api.MediaTurnstile(), []
            running = asyncio.create_task(self._session(gate, log, "dl-running", False, 0.05))
            await asyncio.sleep(0.005)                      # it holds the session
            queued = [asyncio.create_task(self._session(gate, log, f"dl-{n}", False))
                      for n in range(3)]
            await asyncio.sleep(0.005)                      # three downloads queued
            tile = asyncio.create_task(self._session(gate, log, "preview", True))
            await asyncio.gather(running, *queued, tile)
            return log
        log = run(scenario())
        self.assertEqual(log[:2], ["start dl-running", "end dl-running"])
        self.assertEqual(log[2], "start preview",
                         "the tile waited only for the running session")

    def test_without_a_preview_downloads_run_in_arrival_order(self):
        async def scenario():
            gate, log = api.MediaTurnstile(), []
            await asyncio.gather(*[
                self._session(gate, log, f"dl-{n}", False) for n in range(3)])
            return [entry for entry in log if entry.startswith("start")]
        self.assertEqual(run(scenario()), ["start dl-0", "start dl-1", "start dl-2"])

    def test_a_yielding_download_still_runs_once_the_previews_are_done(self):
        async def scenario():
            gate, log = api.MediaTurnstile(), []
            dl = asyncio.create_task(self._session(gate, log, "dl", False))
            await asyncio.sleep(0.002)
            tiles = [asyncio.create_task(self._session(gate, log, f"p{n}", True))
                     for n in range(2)]
            await asyncio.wait_for(asyncio.gather(dl, *tiles), 2)
            return log
        log = run(scenario())
        self.assertIn("end dl", log)

    def test_one_session_at_a_time_whatever_the_mix(self):
        async def scenario():
            gate, depth, worst = api.MediaTurnstile(), [0], [0]
            async def one(interactive):
                async with gate.hold(interactive):
                    depth[0] += 1
                    worst[0] = max(worst[0], depth[0])
                    await asyncio.sleep(0.003)
                    depth[0] -= 1
            await asyncio.gather(*[one(n % 2 == 0) for n in range(8)])
            return worst[0]
        self.assertEqual(run(scenario()), 1)

    def test_locked_reports_the_running_session(self):
        """The coordinator's media health probe reads this to stay away from
        a hub mid-download; the gate has to keep answering it."""
        async def scenario():
            gate = api.MediaTurnstile()
            before = gate.locked()
            async with gate.hold(False):
                during = gate.locked()
            return before, during, gate.locked()
        self.assertEqual(run(scenario()), (False, True, False))


class TheClientUsesIt(unittest.TestCase):
    """iter_recording's `kind` decides the lane: downloads give way, every
    other kind -- previews, camera frames, the health check -- goes first."""

    def _client(self):
        client = api.H500Client("host", "admin", "local", "cloud")
        client.player_id = "player"
        client._super_secret_key = ""
        client._encryption_method = object()
        return client

    def test_a_preview_overtakes_queued_downloads(self):
        """One session at a time, so the order sessions finish in is the
        order they ran in. The kind is recorded by the consumer, because the
        session itself cannot tell a preview from a download -- both fetch
        a recording; only the label differs."""
        from unittest.mock import patch
        client = self._client()
        order = []
        FIN = ("application/json",
               b'{"type":"notification","params":'
               b'{"event_type":"stream_status","status":"finished"}}')

        class _Slow(_StubMediaSession):
            def transceive(self, payload, no_data_timeout=None):
                async def stream():
                    await asyncio.sleep(0.02)   # holds the hub for a while
                    for m, b in self._responses:
                        yield types.SimpleNamespace(mimetype=m, plaintext=b)
                return stream()

        async def consume(kind):
            async for _ in client.iter_recording(CAMERA, 10, 20, kind=kind):
                pass
            order.append(kind)

        async def scenario():
            with patch.object(api, "H500MediaSession", lambda **kw: _Slow([FIN])):
                running = asyncio.create_task(consume("download"))
                await asyncio.sleep(0.005)
                queued = [asyncio.create_task(consume("download")) for _ in range(2)]
                await asyncio.sleep(0.005)
                tile = asyncio.create_task(consume("preview"))
                await asyncio.gather(running, *queued, tile)
            return order
        self.assertEqual(run(scenario()), ["download", "preview", "download", "download"])

if __name__ == "__main__":
    unittest.main()
