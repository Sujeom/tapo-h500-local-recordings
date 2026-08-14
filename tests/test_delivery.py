"""Was that a delivery?

Three things at once: somebody was there, the hub did not recognise them, and
they did not stay -- in daylight. That is a courier far more often than it is
anything else.

The part worth protecting is that it is retrospective. At the moment the hub
reports a detection, the person has been there for one clip and so has
everybody about to stay for ten minutes; the length of a visit is known only
once it is over. A version that answered while the visit was open would say
"delivery" about everyone who arrives.
"""
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
BINARY_SENSOR = (COMPONENT / "binary_sensor.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
NOON, MIDNIGHT = 12, 3
NIGHT_START, NIGHT_END = 22, 6


def mask(*codes):
    value = 0
    for code in codes:
        value |= 1 << (code - 1)
    return value


PERSON = mask(2, 6)
KNOWN = mask(2, 6, 20)
MOTION = mask(2)


def clip(ago, bits=PERSON, length=15):
    return {"startTime": NOW - ago, "endTime": NOW - ago + length,
            "events_1": bits}


def delivery(items, hour=NOON, now=NOW):
    return clips.likely_delivery(
        items, now, const.LOITER_GAP, const.DELIVERY_SECONDS,
        const.DELIVERY_HOLD, hour, NIGHT_START, NIGHT_END)


# A visit that ended three minutes ago: past the gap, inside the hold.
BRIEF = [clip(220), clip(200)]


class Delivery(unittest.TestCase):
    def test_a_brief_daytime_visit_by_a_stranger_counts(self):
        self.assertTrue(delivery(BRIEF))

    def test_somebody_who_stayed_does_not(self):
        """Ten minutes at the door is not a courier. It is the case the
        loitering sensor exists for."""
        stayed = [clip(ago) for ago in range(800, 200, -30)]
        self.assertFalse(delivery(stayed))

    def test_somebody_the_hub_recognised_does_not(self):
        """A member of the household in a hurry."""
        self.assertFalse(delivery([clip(220, KNOWN), clip(200, KNOWN)]))

    def test_and_neither_if_only_one_clip_recognised_them(self):
        """Recognition anywhere in the visit is recognition. Filtering only
        the clips carrying the face code would leave the rest looking like a
        stranger."""
        self.assertFalse(delivery([clip(220, PERSON), clip(200, KNOWN)]))

    def test_motion_alone_does_not(self):
        self.assertFalse(delivery([clip(220, MOTION), clip(200, MOTION)]))

    def test_at_night_it_does_not(self):
        """Somebody at the door at three in the morning is not delivering
        anything, and calling it a delivery is the one wrong answer that
        matters."""
        self.assertFalse(delivery(BRIEF, hour=MIDNIGHT))

    def test_a_visit_still_happening_does_not(self):
        """Its length is not final. Everybody who is about to stay for ten
        minutes has, so far, been there for one clip."""
        self.assertFalse(delivery([clip(60), clip(30)]))

    def test_a_visit_from_this_morning_does_not(self):
        """Past the hold. It stops being news."""
        self.assertFalse(delivery([clip(7200), clip(7180)]))

    def test_nothing_at_all_does_not(self):
        self.assertFalse(delivery([]))

    def test_it_holds_for_a_few_minutes_after_the_visit(self):
        """Long enough for an automation to notice, which is the only reason
        a retrospective signal is usable at all."""
        just_over = const.LOITER_GAP + 30
        self.assertTrue(delivery([clip(just_over + 20), clip(just_over)]))

    def test_and_stops_at_the_end_of_the_hold(self):
        past = const.DELIVERY_HOLD + 60
        self.assertFalse(delivery([clip(past + 20), clip(past)]))


class LastVisit(unittest.TestCase):
    def test_it_reports_the_most_recent_matching_visit(self):
        items = [clip(7200), clip(220), clip(200)]
        found = clips.last_visit(items, const.LOITER_GAP,
                                 lambda c: clips.has_detection(c, {6}))
        self.assertEqual(found, (NOW - 220, NOW - 200 + 15))

    def test_nothing_matching_is_none(self):
        found = clips.last_visit([clip(200, MOTION)], const.LOITER_GAP,
                                 lambda c: clips.has_detection(c, {6}))
        self.assertIsNone(found)


class SharedWithLoitering(unittest.TestCase):
    def test_both_signals_group_visits_the_same_way(self):
        """They are the two ends of one measurement -- too long and too short
        -- and two ways of deciding what a visit is would let a stretch of
        clips be neither."""
        source = (COMPONENT / "clips.py").read_text()
        for name in ("def loitering(", "def likely_delivery("):
            body = source.split(name, 1)[1].split("\n\ndef ", 1)[0]
            self.assertIn("last_visit(", body)


class Entity(unittest.TestCase):
    def test_every_camera_gets_one(self):
        setup = BINARY_SENSOR.split("async_setup_entry", 1)[1].split(
            "\nclass ", 1)[0]
        self.assertIn("H500Delivery(coordinator, index, camera)", setup)

    def test_it_reads_the_configured_night_window(self):
        """Rather than a constant of its own. Two ideas of night is how the
        notification and the sensor come to disagree."""
        body = BINARY_SENSOR.split("class H500Delivery", 1)[1].split(
            "\n\nclass ", 1)[0]
        self.assertIn("CONF_NIGHT_START", body)
        self.assertIn("CONF_NIGHT_END", body)

    def test_it_is_not_dressed_up_as_certainty(self):
        """"Possible", because nothing the hub reports says courier and a
        canvasser looks identical."""
        body = BINARY_SENSOR.split("class H500Delivery", 1)[1].split(
            "\n\nclass ", 1)[0]
        self.assertIn('_attr_translation_key = "possible_delivery"', body)


if __name__ == "__main__":
    unittest.main()
