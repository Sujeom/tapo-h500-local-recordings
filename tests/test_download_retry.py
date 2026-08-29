"""A clip that failed to download gets another chance when the hub recovers.

The media service wedges for hours at a time. Every clip recorded during that
window fails its one download attempt, and `_seen_clips` then skips it
forever -- so a wedge does not merely delay the recordings, it loses them, and
the hub's own copy ages out about a fortnight later.

Retrying immediately is the wrong cure: the whole window failed at once, so
the next poll two seconds later would put every one of them back against a
device that is already refusing. The retry rides the recovery signal instead.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

const = importlib.import_module("tapo_h500.const")

START = 1_786_600_000


def build():
    coord, client = harness._build()
    coord._primed = True
    return coord


class AFailedDownloadIsRemembered(unittest.TestCase):
    def test_a_failure_is_recorded_against_its_clip(self):
        coord = build()
        coord._remember_failed_clip(0, START)
        self.assertEqual(coord._failed_clips[0][START], 1)
        coord._remember_failed_clip(0, START)
        self.assertEqual(coord._failed_clips[0][START], 2)

    def test_recovery_puts_it_back_in_the_queue(self):
        """`_seen_clips` is what makes a clip skipped, so forgetting it there
        is what schedules the retry."""
        coord = build()
        coord._seen_clips[0] = {(START,)}
        coord._remember_failed_clip(0, START)
        coord._retry_failed_clips()
        self.assertNotIn((START,), coord._seen_clips[0])

    def test_a_clip_that_keeps_failing_is_eventually_left_alone(self):
        """Past the limit it is failing for its own reason, not the hub's, and
        each further attempt spends a whole media session on a recording that
        will not arrive."""
        coord = build()
        coord._seen_clips[0] = {(START,)}
        for _ in range(const.DOWNLOAD_RETRY_LIMIT):
            coord._remember_failed_clip(0, START)
        coord._retry_failed_clips()
        self.assertIn((START,), coord._seen_clips[0],
                      "should have given up on this clip")

    def test_the_limit_is_not_off_by_one(self):
        """One attempt short of the limit still retries."""
        coord = build()
        coord._seen_clips[0] = {(START,)}
        for _ in range(const.DOWNLOAD_RETRY_LIMIT - 1):
            coord._remember_failed_clip(0, START)
        coord._retry_failed_clips()
        self.assertNotIn((START,), coord._seen_clips[0])

    def test_a_camera_with_nothing_failed_is_untouched(self):
        coord = build()
        coord._seen_clips[1] = {(START,)}
        coord._retry_failed_clips()
        self.assertIn((START,), coord._seen_clips[1])


class RecoveryDrivesIt(unittest.TestCase):
    def test_bytes_flowing_again_triggers_the_retry(self):
        """note_served_download is the only place that knows the outage is
        over, which is why the retry hangs off it rather than off a timer."""
        coord = build()
        coord._seen_clips[0] = {(START,)}
        coord._remember_failed_clip(0, START)
        # Put the coordinator into the state a wedge leaves it in.
        coord._empty_downloads = 99
        self.assertTrue(coord.media_serving_empty)
        coord.note_served_download()
        self.assertNotIn((START,), coord._seen_clips[0],
                         "recovery must requeue what the outage lost")

    def test_an_ordinary_download_does_not_requeue_everything(self):
        """Only recovery clears them. A routine success while nothing was
        wrong would re-fetch clips that never failed."""
        coord = build()
        coord._seen_clips[0] = {(START,)}
        coord._remember_failed_clip(0, START)
        coord._empty_downloads = 0          # nothing was wrong
        self.assertFalse(coord.media_serving_empty)
        coord.note_served_download()
        self.assertIn((START,), coord._seen_clips[0])


if __name__ == "__main__":
    unittest.main()
