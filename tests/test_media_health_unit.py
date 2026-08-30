"""The media path's state, on its own.

Eleven pieces of coordinator state answered to each other and to nothing
else: the two failure signals, the outage log, the automatic-restart breaker
and the freshness of the evidence they rest on. Spread across a coordinator
that also polls, downloads, tracks faces and manages retention, "is it
wedged, and when did that start" was a question you answered by reading
eleven attributes in four places.

Everything the coordinator's own tests drive through a poll, these drive
directly -- which is the point of the object existing.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

media_health = importlib.import_module("tapo_h500.media_health")
const = importlib.import_module("tapo_h500.const")
dt_util = sys.modules["homeassistant.util.dt"]
NOW = dt_util.utcnow().timestamp()
HOUR = 3600


class TheTwoSignals(unittest.TestCase):
    def setUp(self):
        self.media = media_health.MediaHealth()

    def test_a_fresh_one_is_healthy_and_has_seen_nothing(self):
        self.assertFalse(self.media.wedged)
        self.assertIsNone(self.media.status)
        self.assertEqual(self.media.wedges, [])
        self.assertEqual(self.media.healthy_seconds, 0.0)

    def test_the_handshake_alone_is_enough(self):
        self.media.note_status("wedged")
        self.assertTrue(self.media.wedged)

    def test_two_empty_downloads_alone_are_enough(self):
        """The sentinel may never have run. The downloads are their own
        evidence, and that is the outage people actually notice."""
        self.media.note_empty()
        self.assertFalse(self.media.wedged)
        self.media.note_empty()
        self.assertTrue(self.media.wedged)

    def test_one_empty_download_is_a_freak_clip(self):
        self.media.note_empty()
        self.assertFalse(self.media.serving_empty)

    def test_the_two_do_not_double_count(self):
        self.media.note_empty()
        self.media.note_empty()
        self.media.note_status("wedged")
        self.assertEqual(len(self.media.wedges), 1)

    def test_bytes_end_it_and_say_so(self):
        self.media.note_empty()
        self.media.note_empty()
        self.assertTrue(self.media.note_served(), "that was a recovery")
        self.assertFalse(self.media.wedged)
        self.assertFalse(self.media.note_served(), "and this one is routine")

    def test_a_healthy_handshake_after_a_wedge_says_so(self):
        self.media.note_status("wedged")
        self.assertTrue(self.media.note_status("healthy"))
        self.assertFalse(self.media.note_status("healthy"))

    def test_the_inconclusive_verdicts_do_not_alarm(self):
        for verdict in ("unreachable", "silent"):
            with self.subTest(verdict):
                media = media_health.MediaHealth()
                media.note_status(verdict)
                self.assertFalse(media.wedged)


class TheBreaker(unittest.TestCase):
    def test_bytes_re_arm_it(self):
        media = media_health.MediaHealth()
        media.restart_broken = True
        media.note_served()
        self.assertFalse(media.restart_broken)

    def test_a_restart_is_a_treatment_not_a_recovery(self):
        """It borrowed the served-download notification to reset its
        counters. That one means bytes arrived, and it would close the
        outage -- reporting a cure at the moment one was being attempted."""
        media = media_health.MediaHealth()
        media.note_empty()
        media.note_empty()
        media.note_restarting()
        self.assertIsNone(media.wedges[-1]["ended"])
        self.assertEqual([a["what"] for a in media.wedges[-1]["tried"]],
                         ["hub restart"])
        self.assertEqual(media._empty, 0, "it still starts counting fresh")


class TheClock(unittest.TestCase):
    def test_it_climbs_while_the_hub_serves(self):
        media = media_health.MediaHealth()
        media._healthy_since = NOW - 5 * HOUR
        self.assertAlmostEqual(media.healthy_seconds, 5 * HOUR, places=3)

    def test_it_is_zero_while_wedged(self):
        media = media_health.MediaHealth()
        media._healthy_since = NOW - 5 * HOUR
        media.note_status("wedged")
        self.assertEqual(media.healthy_seconds, 0.0)

    def test_the_best_run_survives_the_wedge_that_ended_it(self):
        media = media_health.MediaHealth()
        media._healthy_since = NOW - 12 * HOUR
        media.note_status("wedged")
        media.note_status("healthy")
        self.assertAlmostEqual(media.longest_healthy_seconds, 12 * HOUR,
                               places=3)

    def test_counts_are_windowed(self):
        media = media_health.MediaHealth()
        media.wedges = [{"at": NOW - 8 * 86400, "tried": [], "ended": None},
                        {"at": NOW - 3 * 86400, "tried": [], "ended": None},
                        {"at": NOW - HOUR, "tried": [], "ended": None}]
        self.assertEqual(media.wedges_since(7 * 86400), 2)
        self.assertEqual(media.wedges_since(86400), 1)

    def test_the_log_does_not_grow_without_end(self):
        media = media_health.MediaHealth()
        media.wedges = [
            {"at": NOW - const.WEDGE_HISTORY_SECONDS - 1, "tried": [],
             "ended": None},
            {"at": NOW - 86400, "tried": [], "ended": None}]
        media.note_status("wedged")
        self.assertEqual(len(media.wedges), 2)


class TheCoordinatorStillSpeaksForIt(unittest.TestCase):
    """Entities, repairs and diagnostics hold a coordinator and nothing
    else. Renaming what they read would be a rename in nine files."""

    def setUp(self):
        self.coord, _ = harness._build()

    def test_the_readings_come_through(self):
        self.coord.media.note_status("wedged")
        self.assertEqual(self.coord.media_status, "wedged")
        self.assertTrue(self.coord.media_wedged)
        self.assertEqual(self.coord.healthy_seconds, 0.0)
        self.assertEqual(len(self.coord.wedges), 1)
        self.assertEqual(self.coord.wedges_since(HOUR), 1)
        self.assertEqual(len(self.coord.recovery_log()), 1)

    def test_it_is_the_same_object_not_a_copy(self):
        self.coord.note_recovery_attempt("hub restart")
        self.coord.media.note_status("wedged")
        self.coord.note_recovery_attempt("player id rotated")
        self.assertEqual([a["what"] for a in self.coord.media.wedges[-1]["tried"]],
                         ["player id rotated"])

    def test_recovery_still_clears_the_frame_marks(self):
        """The one thing the coordinator keeps for itself: those marks are
        about pictures, not about the media path."""
        self.coord._frame_attempts[0] = (1, None)
        self.coord.note_media_status("wedged")
        self.coord.note_media_status("healthy")
        self.assertEqual(self.coord._frame_attempts, {})


if __name__ == "__main__":
    unittest.main()
