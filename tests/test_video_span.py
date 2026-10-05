"""Seconds of video a stream has carried, read off its own clock.

The hub never says how big a clip is, so bytes cannot be turned into a
percentage. The MPEG-TS it streams carries presentation timestamps, and
those against the clip's indexed length are the only honest progress bar.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402,F401  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")


def _ts(pid, payload, start=True):
    """One 188-byte MPEG-TS packet carrying `payload`, stuffed to size."""
    header = bytes([0x47, (0x40 if start else 0) | (pid >> 8), pid & 0xFF,
                    0x10])
    return header + payload[:184] + b"\xff" * (184 - len(payload[:184]))


def _pes(pts, stream_id=0xE0):
    """The opening of a PES packet whose header carries this PTS."""
    return b"\x00\x00\x01" + bytes([stream_id, 0, 0, 0x80, 0x80, 5]) + bytes([
        0x21 | ((pts >> 29) & 0x0E), (pts >> 22) & 0xFF,
        ((pts >> 14) & 0xFE) | 1, (pts >> 7) & 0xFF, ((pts << 1) & 0xFE) | 1])


def frame(seconds, pid=0x44, stream_id=0xE0):
    return _ts(pid, _pes(int(seconds * 90_000), stream_id))


class VideoSpan(unittest.TestCase):
    def test_it_reads_the_video_clock(self):
        span = clips.VideoSpan()
        span.feed(frame(0) + frame(1) + frame(2))
        self.assertEqual(span.seconds, 2.0)

    def test_audio_does_not_count(self):
        span = clips.VideoSpan()
        span.feed(frame(0) + frame(9, pid=0x54, stream_id=0xC0) + frame(1))
        self.assertEqual(span.seconds, 1.0)

    def test_two_lenses_over_one_interval_count_once(self):
        """A dual-lens camera sends both lenses on their own PIDs, over the
        same seconds."""
        span = clips.VideoSpan()
        span.feed(frame(0) + frame(0, pid=0x64) + frame(3) + frame(3, pid=0x64))
        self.assertEqual(span.seconds, 3.0)

    def test_packets_split_across_chunks_are_reassembled(self):
        data = frame(0) + frame(1) + frame(2)
        span = clips.VideoSpan()
        for offset in range(0, len(data), 100):
            span.feed(data[offset:offset + 100])
        self.assertEqual(span.seconds, 2.0)

    def test_a_clock_that_wraps_keeps_counting(self):
        """The PTS is 33 bits; a clip can straddle the wrap."""
        span = clips.VideoSpan()
        span.feed(_ts(0x44, _pes((1 << 33) - 45_000)) + _ts(0x44, _pes(45_000)))
        self.assertEqual(span.seconds, 1.0)

    def test_it_finds_the_packets_after_stray_bytes(self):
        span = clips.VideoSpan()
        span.feed(b"\x00\x01" + frame(0) + frame(2))
        self.assertEqual(span.seconds, 2.0)

    def test_feed_returns_the_running_total(self):
        span = clips.VideoSpan()
        self.assertEqual(span.feed(frame(0)), 0.0)
        self.assertEqual(span.feed(frame(4)), 4.0)


if __name__ == "__main__":
    unittest.main()
