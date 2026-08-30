"""The camera entity shows the event you were just notified about.

The notification's Camera button opens `camera.<doorbell>`, which used to
serve the newest thumbnail on disk -- and a thumbnail is written by a
download. So for the first half-minute of every event, and forever when the
newest clip is never downloaded (rings-only mode, a download-type filter, a
failed download), the button showed the *previous* event. Old photo, every
time, at exactly the moment a person presses it.

The coordinator closes that gap: when the newest indexed clip's frame is not
on disk, it is fetched from the hub once through the preview machinery, which
caches at exactly the path the download would use. These tests pin the shape
of that:

- one attempt per clip, marked before it starts, because this is called from
  the frontend on every look at the picture and a hub that refused once must
  not be asked per poll -- each ask is a whole media session against a device
  that is easy to overload;
- a newer clip gets its own attempt, so one failure does not stick forever;
- nothing is attempted while the hub is still recording (no indexed clip),
  because no frame of an unindexed clip exists anywhere to fetch.
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

CAMERA = {"device_id": "cam0", "alias": "Front"}
NOW = 1_786_600_000


def clip(start, length=15):
    return {"startTime": start, "endTime": start + length}


class LatestFrame(unittest.TestCase):
    def setUp(self):
        self.coord, _ = harness._build()
        self.clips: list[dict] = []
        self.coord.clips_for = lambda index: list(self.clips)
        self.fetched: list[int] = []
        self.served = 0

        async def fake_preview(hass, client, camera, start_time):
            self.fetched.append(start_time)
            return None

        async def fake_scan(hass, camera):
            self.served += 1
            return b"newest-on-disk"

        async def fake_sweep(hass, camera):
            self.swept.append(camera["device_id"])
            return []

        self.swept: list[str] = []
        self._patch("async_preview_clip", fake_preview)
        self._patch("async_latest_image", fake_scan)
        self._patch("async_prune_previews", fake_sweep)

    def _patch(self, name, value):
        self.addCleanup(setattr, coordinator_mod, name,
                        getattr(coordinator_mod, name))
        setattr(coordinator_mod, name, value)

    def _frame(self):
        return asyncio.run(self.coord.async_latest_frame(0, CAMERA))

    def test_the_newest_clips_frame_is_fetched_then_served(self):
        self.clips = [clip(NOW - 300), clip(NOW - 30)]
        self.assertEqual(self._frame(), b"newest-on-disk")
        self.assertEqual(self.fetched, [NOW - 30],
                         "the newest indexed clip is the one on the "
                         "notification, so it is the one to fetch")
        self.assertEqual(self.served, 1)

    def test_one_attempt_per_clip_even_when_it_failed(self):
        """The fake fetch returns None -- a hub that would not serve it.

        Asking again on the next look would retry per frontend poll, which is
        a media-session storm against a hub that wedges. Stale is the correct
        price; the next clip resets it.
        """
        self.clips = [clip(NOW - 30)]
        self._frame()
        self._frame()
        self._frame()
        self.assertEqual(self.fetched, [NOW - 30])

    def test_a_newer_clip_gets_its_own_attempt(self):
        self.clips = [clip(NOW - 300)]
        self._frame()
        self.clips = [clip(NOW - 300), clip(NOW - 30)]
        self._frame()
        self.assertEqual(self.fetched, [NOW - 300, NOW - 30])

    def test_concurrent_looks_collapse_to_one_attempt(self):
        """Marked before the await, so the frontend polling the picture while
        a fetch is in flight does not open a second media session."""
        self.clips = [clip(NOW - 30)]
        waited = []

        async def slow_preview(hass, client, camera, start_time):
            waited.append(start_time)
            await asyncio.sleep(0)
            return None

        self._patch("async_preview_clip", slow_preview)

        async def both():
            await asyncio.gather(
                self.coord.async_latest_frame(0, CAMERA),
                self.coord.async_latest_frame(0, CAMERA))

        asyncio.run(both())
        self.assertEqual(waited, [NOW - 30])

    def test_still_recording_means_nothing_to_fetch(self):
        """No indexed clip: the hub is still recording the event, and no
        frame of it exists anywhere. Serve what there is, ask for nothing."""
        self.clips = []
        self.assertEqual(self._frame(), b"newest-on-disk")
        self.assertEqual(self.fetched, [])

    def test_a_malformed_clip_is_not_asked_for(self):
        """The same guard _download applies: no usable start and end, no
        media session."""
        self.clips = [{"startTime": NOW}, {"endTime": NOW},
                      {"startTime": NOW, "endTime": NOW}]
        self._frame()
        self.assertEqual(self.fetched, [])

    def test_cameras_are_tracked_separately(self):
        self.clips = [clip(NOW - 30)]
        asyncio.run(self.coord.async_latest_frame(0, CAMERA))
        asyncio.run(self.coord.async_latest_frame(1, CAMERA))
        self.assertEqual(self.fetched, [NOW - 30, NOW - 30],
                         "camera 1's attempt must not be blocked by camera "
                         "0 having attempted the same start time")


class Routing(unittest.TestCase):
    """Both entities that promise "the newest clip's frame" go through the
    coordinator, which is the only place that knows what the newest clip is.
    """

    CAMERA_SRC = (COMPONENT / "camera.py").read_text()
    IMAGE_SRC = (COMPONENT / "image.py").read_text()

    def test_the_camera_entity_asks_the_coordinator(self):
        self.assertIn("async_latest_frame", self.CAMERA_SRC)
        self.assertNotIn("async_latest_image", self.CAMERA_SRC,
                         "a direct disk scan is the old photo bug")

    def test_the_latest_event_image_asks_the_coordinator(self):
        body = self.IMAGE_SRC.split("class H500ContactSheet", 1)[0]
        self.assertIn("async_latest_frame", body)
        self.assertNotIn("async_latest_image", self.IMAGE_SRC)

    def test_the_latest_event_image_restamps_when_the_frame_lands(self):
        """It stamps on the detection, several seconds before any frame of
        that event exists -- so a dashboard fetched once, got the previous
        frame, and was never told again when the real one arrived. The
        download signal is fired exactly when the file is written, so it
        stamps a second time."""
        body = self.IMAGE_SRC.split("class H500ContactSheet", 1)[0]
        self.assertIn('signal("event"', body)
        self.assertIn('signal("image"', body)


class HealsWithTheHub(unittest.TestCase):
    """Attempt marks clear when media recovers, so the picture self-repairs.

    Every clip that occurs during a media outage burns its one fetch
    attempt on an empty answer. Without this, the camera picture stays
    stale after recovery until the NEXT event -- the stale-image complaint,
    scheduled to recur. Only recovery clears the marks: a routine served
    download must not, or every download would invite a redundant refetch.
    """

    # A stand-in for a real (start time, fetch task) mark. Nothing here runs
    # the fetch; only whether the mark survives is under test.
    MARK = (NOW - 30, None)

    def _flagged(self, coord):
        coord.note_empty_download()
        coord.note_empty_download()

    def test_recovery_from_hollow_sessions_clears_the_marks(self):
        coord, _ = harness._build()
        coord._frame_attempts[0] = self.MARK
        self._flagged(coord)
        coord.note_served_download()
        self.assertEqual(coord._frame_attempts, {})

    def test_recovery_from_the_wedge_clears_the_marks(self):
        coord, _ = harness._build()
        coord._frame_attempts[0] = self.MARK
        coord.media.status = "wedged"
        coord.note_media_status("healthy")
        self.assertEqual(coord._frame_attempts, {})

    def test_a_routine_download_does_not(self):
        coord, _ = harness._build()
        coord._frame_attempts[0] = self.MARK
        coord.note_served_download()
        self.assertEqual(coord._frame_attempts, {0: self.MARK})

    def test_staying_healthy_does_not_either(self):
        coord, _ = harness._build()
        coord._frame_attempts[0] = self.MARK
        coord.media.status = "healthy"
        coord.note_media_status("healthy")
        self.assertEqual(coord._frame_attempts, {0: self.MARK})


IMAGE_SOURCE = (COMPONENT / "image.py").read_text()


def _stamp_code() -> str:
    """The BODY of _stamp with its docstring removed.

    The docstring explains the old bug and therefore contains the very text
    these tests search for; matching against it would pass on prose alone.
    """
    body = IMAGE_SOURCE.split("class H500EventImage", 1)[1].split(
        "\nclass ", 1)[0]
    stamp = body.split("def _stamp", 1)[1].split("async def", 1)[0]
    return stamp.split('"""')[-1] if '"""' in stamp else stamp


class ThePictureSaysHowOldItIs(unittest.TestCase):
    """A wedged camera serves its last frame forever, and a still picture
    cannot say so itself. The timestamp has to.

    `image_last_updated` was stamped with `utcnow()` -- the moment Home
    Assistant was told to look -- so a frame from last night reported itself
    as seconds old. The module docstring says the timestamp exists so
    "anyone looking at it can see how old it is instead of wondering whether
    they are looking at now", which is exactly what stamping now() defeats.
    """

    def test_the_stamp_is_the_events_moment_not_the_lookup(self):
        code = _stamp_code()
        self.assertIn("last_activity", code)
        self.assertIn("utc_from_timestamp", code)

    def test_now_survives_only_as_the_no_activity_fallback(self):
        """A camera that has produced nothing has no truer answer -- but
        now() must not be reachable when an event exists, or the bug is
        back."""
        code = _stamp_code()
        head, sep, tail = code.partition("else")
        self.assertTrue(sep, "the fallback must be an explicit else branch")
        self.assertNotIn("utcnow()", head,
                         "now() must not answer when an event exists")
        self.assertIn("utcnow()", tail)

    def test_the_age_is_published_for_a_person_to_read(self):
        body = IMAGE_SOURCE.split("class H500EventImage", 1)[1].split(
            "\nclass ", 1)[0]
        attrs = body.split("extra_state_attributes", 1)[1]
        self.assertIn("frame_taken", attrs)
        self.assertIn("frame_age_seconds", attrs)

    def test_the_coordinator_answers_what_the_stamp_asks_for(self):
        """The stamp reads last_activity, so it must mean the newest event."""
        coord, _ = harness._build()
        coord.clips_for = lambda index: [clip(NOW - 3600), clip(NOW - 600)]
        self.assertEqual(coord.last_activity(0), NOW - 600)
        coord.clips_for = lambda index: []
        self.assertIsNone(coord.last_activity(0))


class SharedFetch(unittest.TestCase):
    """The frontend asks for both pictures at once.

    `camera.<doorbell>` and the latest-event image both promise the newest
    clip's frame, and a dashboard showing both asks for both together. One
    attempt per clip is right, but a second look that merely *skips* the fetch
    reads the file while the first is still writing it -- and gets the old
    frame, from the bookkeeping that exists to prevent exactly that.
    """

    def setUp(self):
        self.coord, _ = harness._build()
        self.clips = [clip(NOW - 30)]
        self.coord.clips_for = lambda index: list(self.clips)
        self.on_disk = b"the previous event"
        self.finished: list[int] = []

        async def read(hass, camera):
            return self.on_disk

        async def sweep(hass, camera):
            return []

        LatestFrame._patch(self, "async_latest_image", read)
        LatestFrame._patch(self, "async_prune_previews", sweep)

    _patch = LatestFrame._patch

    def test_a_look_arriving_mid_fetch_waits_for_it(self):
        async def slow_preview(hass, client, camera, start_time):
            await asyncio.sleep(0)
            self.on_disk = b"this event"
            self.finished.append(start_time)
            return None

        self._patch("async_preview_clip", slow_preview)

        async def both():
            return await asyncio.gather(
                self.coord.async_latest_frame(0, CAMERA),
                self.coord.async_latest_frame(0, CAMERA))

        self.assertEqual(asyncio.run(both()),
                         [b"this event", b"this event"])
        self.assertEqual(self.finished, [NOW - 30], "still one fetch")

    def test_a_viewer_going_away_does_not_cancel_the_fetch(self):
        """Cancelling the fetch would be worse than not starting it: the
        attempt is already marked, so the clip would be recorded as tried and
        never actually tried, and the old frame would stay until the next
        event."""
        async def slow_preview(hass, client, camera, start_time):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.finished.append(start_time)
            return None

        self._patch("async_preview_clip", slow_preview)

        async def scenario():
            looker = asyncio.ensure_future(
                self.coord.async_latest_frame(0, CAMERA))
            await asyncio.sleep(0)
            looker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await looker
            await self.coord._frame_attempts[0][1]

        asyncio.run(scenario())
        self.assertEqual(self.finished, [NOW - 30])


if __name__ == "__main__":
    unittest.main()
