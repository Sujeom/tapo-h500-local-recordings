"""The hollow-session failure is found within the hour, even on a quiet day.

The serving-empty state was only detectable through failed downloads, so it
needed events to happen and auto-download to cover them -- on a quiet
afternoon the hub could serve nothing for hours, photos failing silently.
Now, when no media session has produced evidence for an hour and an indexed
clip exists, the coordinator fetches two bounded seconds of the newest clip
itself, labelled (healthcheck) in the session log, and feeds the result to
the same counters the downloads feed. Recovery clears the same way: an
hourly fetch that comes back with bytes is the all-clear, no download
needed.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000


class _Client(harness._Client):
    def __init__(self, chunks):
        super().__init__()
        self.chunks = chunks
        self.fetches = []

    def recent(self, camera, start, end):
        self.calls.append("recent")
        return [{"startTime": NOW - 120, "endTime": NOW - 90}]

    async def iter_recording(self, camera, start, end, kind="download"):
        self.fetches.append((start, end, kind))
        for _ in range(self.chunks):
            yield b"\x47" * 188


class _Task:
    """Captures background tasks the way the entry stub must."""

    def __init__(self, entry):
        entry.tasks = []
        entry.async_create_background_task = (
            lambda hass, coro, name: entry.tasks.append(coro))


coordinator_mod.EMPTY_CONFIRM_DELAY = 0  # no real sleeping in tests


def _build(chunks=2):
    client = _Client(chunks)
    entry = harness._Entry(20)
    _Task(entry)
    coord = coordinator_mod.H500Coordinator(harness._Hass(), entry, client)
    coord._download_new = lambda *a, **k: None
    return coord, client, entry


def _poll(coord, entry):
    asyncio.run(coord._async_update_data())
    for coro in entry.tasks:
        asyncio.run(coro)
    entry.tasks.clear()


class DeepCheck(unittest.TestCase):
    def test_a_quiet_hour_triggers_one_bounded_fetch(self):
        coord, client, entry = _build()
        coord.media.evidence_at = NOW - 3700
        _poll(coord, entry)
        self.assertEqual(len(client.fetches), 1)
        start, end, kind = client.fetches[0]
        self.assertEqual(kind, "healthcheck")
        self.assertLessEqual(end - start, 2)

    def test_fresh_evidence_means_no_fetch(self):
        coord, client, entry = _build()
        coord.media.evidence_at = NOW - 60
        _poll(coord, entry)
        self.assertEqual(client.fetches, [])

    def test_bytes_are_the_all_clear(self):
        coord, client, entry = _build()
        coord.note_empty_download()
        coord.note_empty_download()
        coord.media.evidence_at = NOW - 3700
        _poll(coord, entry)
        self.assertFalse(coord.media_serving_empty)

    def test_an_empty_fetch_confirms_itself_before_flagging(self):
        """One empty answer could be a freak clip; the check fetches again
        inside the same task, so the state is flagged within minutes -- not
        after a second quiet hour."""
        coord, client, entry = _build(chunks=0)
        coord.media.evidence_at = NOW - 3700
        _poll(coord, entry)
        self.assertEqual(len(client.fetches), 2)
        self.assertTrue(coord.media_serving_empty)

    def test_no_indexed_clips_means_nothing_to_ask(self):
        coord, client, entry = _build()
        client.recent = lambda camera, start, end: (
            client.calls.append("recent") or [])
        coord.media.evidence_at = NOW - 3700
        _poll(coord, entry)
        self.assertEqual(client.fetches, [])

    def test_evidence_freshens_so_the_next_hour_is_quiet_again(self):
        coord, client, entry = _build()
        coord.media.evidence_at = NOW - 3700
        _poll(coord, entry)
        _poll(coord, entry)
        self.assertEqual(len(client.fetches), 1,
                         "the check must not run per poll once evidence "
                         "is fresh")

    def test_downloads_already_count_as_evidence(self):
        coord, client, entry = _build()
        coord.media.evidence_at = NOW - 3700
        coord.note_served_download()
        _poll(coord, entry)
        self.assertEqual(client.fetches, [])

    def test_a_fetch_that_raises_is_inconclusive_not_fatal(self):
        coord, client, entry = _build()

        async def boom(camera, start, end, kind="download"):
            raise OSError("hub going down")
            yield  # pragma: no cover - generator shape

        client.iter_recording = boom
        coord.media.evidence_at = NOW - 3700
        _poll(coord, entry)  # must not raise
        self.assertFalse(coord.media_serving_empty)

    def test_a_client_without_the_call_is_skipped(self):
        """The harness's other sixteen fake clients have no iter_recording
        and must keep working untouched."""
        coord, client, entry = _build()
        client.iter_recording = None
        coord.media.evidence_at = NOW - 3700
        _poll(coord, entry)  # must not raise
        self.assertEqual(entry.tasks, [])


if __name__ == "__main__":
    unittest.main()
