"""What was different about the day, rather than what was in it.

The digest counts, which is the honest thing to do and not what anybody reads
one for. "Front: 48 recordings (12 person, 3 vehicle)" is the same sentence
every day, so a day worth knowing about looks exactly like a day that was not.

The rule these follow is that an empty list is the normal answer. A highlights
line that appears every day is not a highlight, and every assertion below is
either "this got mentioned" or -- more often the point -- "this did not".
"""
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
INIT = (COMPONENT / "__init__.py").read_text()
INTENT = (COMPONENT / "intent.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
DAY = const.LOOKBACK_SECONDS
NIGHT = (const.DEFAULT_NIGHT_START, const.DEFAULT_NIGHT_END)


def mask(*codes):
    value = 0
    for code in codes:
        value |= 1 << (code - 1)
    return value


PERSON = mask(2, 6)
STRANGER = mask(2, 6, 22)
TAMPER = mask(6, 19, 20)


def clip(when, events=PERSON, seconds=15):
    return {"startTime": when, "endTime": when + seconds, "events_1": events}


def at_hour(hour, minute=0):
    """A moment at this LOCAL hour today, in the harness's -07:00 zone."""
    for back in range(0, 24):
        moment = NOW - back * 3600
        if clips._local_hour(moment) == hour:
            return moment - moment % 3600 + minute * 60
    raise AssertionError(f"no {hour}:00 within the window")


def said(per_camera, **kwargs):
    return clips.highlights(per_camera, NOW, DAY, *NIGHT, **kwargs)


class Quiet(unittest.TestCase):
    def test_an_ordinary_day_says_nothing(self):
        """The most important case. A list with something in it every day is
        not a list of highlights."""
        spread = [clip(NOW - 3600 * step - 60) for step in range(6)]
        self.assertEqual(said({"Front": spread}), [])

    def test_a_camera_that_recorded_nothing_is_worth_saying(self):
        self.assertEqual(said({"Front": []}), ["Front recorded nothing"])

    def test_quiet_cameras_are_named_together(self):
        """One line, not one each: three silent cameras on a three-camera hub
        is one fact."""
        lines = said({"Front": [], "Side": [], "Back": []})
        self.assertEqual(len(lines), 1)
        self.assertIn("Back, Front, Side", lines[0])

    def test_nothing_at_all_is_an_empty_list(self):
        self.assertEqual(said({}), [])

    def test_recordings_older_than_the_window_do_not_count(self):
        self.assertEqual(said({"Front": [clip(NOW - DAY * 2)]}),
                         ["Front recorded nothing"])


class Peaks(unittest.TestCase):
    def test_a_real_peak_is_mentioned_with_the_hour(self):
        burst = [clip(at_hour(15) + step * 60) for step in range(8)]
        line = said({"Front": burst})[0]
        self.assertIn("busiest around 3pm", line)
        self.assertIn("8 recordings", line)

    def test_a_flat_day_has_no_peak(self):
        """Twenty-four recordings, one an hour. Nothing happened at any
        particular time and saying otherwise is noise."""
        flat = [clip(at_hour(hour) + 60) for hour in range(24)]
        self.assertEqual([line for line in said({"Front": flat})
                          if "busiest" in line], [])

    def test_a_busy_camera_needs_a_bigger_peak_than_a_quiet_one(self):
        """A doorbell on a pavement sees five people an hour all day. Its
        busiest hour is five, and five is not news there -- where it would be
        on a back gate. A fixed floor alone says "busiest around 3pm" every
        single day, which is the failure this whole function exists to avoid.
        """
        steady = [clip(at_hour(hour) + step * 60)
                  for hour in range(24) for step in range(5)]
        self.assertEqual([line for line in said({"Front": steady})
                          if "busiest" in line], [])

    def test_a_handful_of_recordings_is_not_a_peak(self):
        """A camera with three events all afternoon has a maximum hour, and it
        means nothing. The floor is the same one the unusual sensor uses."""
        few = [clip(at_hour(15) + step * 60) for step in range(3)]
        self.assertEqual([line for line in said({"Front": few})
                          if "busiest" in line], [])

    def test_the_hour_is_local(self):
        """A peak reported in UTC sends somebody to the wrong part of the
        recording."""
        burst = [clip(at_hour(15) + step * 60) for step in range(8)]
        self.assertIn("3pm", said({"Front": burst})[0])

    def test_midnight_and_midday_are_words(self):
        self.assertEqual(clips._clock(0), "midnight")
        self.assertEqual(clips._clock(12), "midday")
        self.assertEqual(clips._clock(13), "1pm")
        self.assertEqual(clips._clock(1), "1am")


class AfterDark(unittest.TestCase):
    def test_an_unfamiliar_face_at_night_is_mentioned(self):
        night = [clip(at_hour(2) + step * 60, STRANGER) for step in range(2)]
        line = [text for text in said({"Front": night}) if "unfamiliar" in text]
        self.assertEqual(len(line), 1)
        self.assertIn("2 unfamiliar faces", line[0])

    def test_the_same_face_in_daylight_is_not(self):
        """An unknown face at three in the afternoon is a delivery. It is the
        clock that makes it worth mentioning, which is the whole reason the
        night window is configurable."""
        day = [clip(at_hour(15) + step * 60, STRANGER) for step in range(2)]
        self.assertEqual([text for text in said({"Front": day})
                          if "unfamiliar" in text], [])

    def test_a_recognised_face_at_night_is_not(self):
        """Somebody coming home late is not news."""
        night = [clip(at_hour(2) + step * 60, PERSON) for step in range(2)]
        self.assertEqual([text for text in said({"Front": night})
                          if "unfamiliar" in text], [])

    def test_one_face_is_singular(self):
        night = [clip(at_hour(2), STRANGER)]
        line = [text for text in said({"Front": night}) if "unfamiliar" in text]
        self.assertIn("1 unfamiliar face at", line[0])
        self.assertNotIn("faces", line[0])


class Waiting(unittest.TestCase):
    def test_somebody_who_stayed_is_mentioned(self):
        run = [clip(NOW - 900 + step * 30) for step in range(12)]
        line = [text for text in said({"Front": run}) if "somebody" in text]
        self.assertEqual(len(line), 1)
        self.assertIn("minutes", line[0])

    def test_somebody_passing_through_is_not(self):
        """Two clips thirty seconds apart is somebody walking past."""
        brief = [clip(NOW - 900), clip(NOW - 870)]
        self.assertEqual([text for text in said({"Front": brief})
                          if "somebody" in text], [])

    def test_a_busy_day_of_separate_visits_is_not_one_long_one(self):
        """Twelve visitors spread over the day must not add up to somebody
        standing at the door for hours."""
        spread = [clip(NOW - 3600 * step - 60) for step in range(12)]
        self.assertEqual([text for text in said({"Front": spread})
                          if "somebody" in text], [])


class Tampering(unittest.TestCase):
    def test_it_is_mentioned(self):
        self.assertIn("tampered", " ".join(said({"Front": [clip(NOW - 60, TAMPER)]})))

    def test_it_comes_first(self):
        """However far down the list its camera's name would put it. A digest
        that buries this under "Side was busiest around 3pm" has failed."""
        lines = said({"Side": [clip(at_hour(15) + step * 60) for step in range(8)],
                      "Zed": [clip(NOW - 60, TAMPER)]})
        self.assertIn("tampered", lines[0])

    def test_an_ordinary_recording_does_not_trigger_it(self):
        self.assertNotIn("tampered",
                         " ".join(said({"Front": [clip(NOW - 60, PERSON)]})))


class Wiring(unittest.TestCase):
    def test_the_digest_returns_them(self):
        body = INIT.split("async def daily_summary", 1)[1].split("async def ", 1)[0]
        self.assertIn('"highlights": highlights(', body)

    def test_the_digest_uses_the_configured_night_window(self):
        body = INIT.split("async def daily_summary", 1)[1].split("async def ", 1)[0]
        self.assertIn("CONF_NIGHT_START", body)

    def test_the_spoken_answer_leads_with_them(self):
        """Spoken aloud, a bare list of totals is the same sentence every day
        and the interesting one sounds exactly like the dull one."""
        body = INTENT.split("class TodayIntent", 1)[1]
        self.assertIn("notable", body)
        self.assertIn('f"{\'. \'.join(notable)}. {spoken}"', body)

    def test_the_spoken_answer_says_nothing_extra_on_a_dull_day(self):
        body = INTENT.split("class TodayIntent", 1)[1]
        self.assertIn("if notable:", body)


if __name__ == "__main__":
    unittest.main()
