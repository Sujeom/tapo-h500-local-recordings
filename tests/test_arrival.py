"""Arriving is not the same as being detected.

The ordinary detection event fires every time anyone crosses a camera. For a
household that is the wrong grain: someone who works from home trips the front
camera a dozen times a day, and only the first is news. These tests drive the
real coordinator poll and check that exactly one arrival is announced per named
person per local day -- and, importantly, that a restart does not replay the
morning.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
clips_mod = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
DAY = 86_400


def sighting(face_id, when):
    """One recording carrying a recognised face, in the hub's own shape.

    Nested under event_info deliberately: a flat face_ids key is what the
    service hands back, not what the hub sends, and testing against the wrong
    shape has produced a passing test over broken code here before.
    """
    return {"startTime": when, "endTime": when + 10,
            "events_1": 1 << (20 - 1),
            "event_info": [{"face_id": face_id}]}


class _Client:
    """Returns whatever the test has queued as this poll's recordings."""

    def __init__(self):
        self.clips = []

    def cameras(self):
        return [{"device_id": "cam0", "alias": "Front"}]

    def recent(self, camera, start, end):
        return list(self.clips)

    def detections(self, camera, start, end):
        return list(self.clips)

    def hub_status(self):
        return {}


def build(names=None):
    client = _Client()
    entry = harness._Entry(20, face_names=names or {})
    hass = harness._Hass()
    coord = coordinator_mod.H500Coordinator(hass, entry, client)
    coord._download_new = lambda *a, **k: None
    return coord, client, hass


def poll(coord):
    """One refresh, publishing the result the way the real base class does.

    _async_update_data only computes; DataUpdateCoordinator is what assigns
    self.data afterwards. Driving the method directly without this leaves
    every later read looking at None.
    """
    coord.data = asyncio.run(coord._async_update_data())
    return coord.data


def announce(coord, clips):
    """Run the arrival check directly, with nothing swallowing its errors.

    _poll wraps this in a broad except so a bad clip can never cost a poll,
    which is right in production and useless in a test: a mutation that made
    the check raise passed as "no arrivals announced". Driving the method
    itself means a break shows up as a break.
    """
    coord._primed = True
    coord._note_arrivals({0: clips})


def arrivals(hass):
    return [data for name, data in hass.bus.fired
            if name == const.EVENT_ARRIVAL]


def earlier_today(hours=6):
    """A moment that many hours ago, pulled forward if it lands on yesterday.

    A fixed offset is not portable: six hours before the test's NOW is the
    previous local day in half the world's timezones, and a test asserting
    "nothing was announced" passes trivially when the sighting was not from
    today in the first place. That is exactly how a broken priming guard
    survived here once.
    """
    today = clips_mod.local_date(NOW)
    for back in range(hours, 0, -1):
        if clips_mod.local_date(NOW - back * 3600) == today:
            return NOW - back * 3600
    return NOW - 60


def yesterday():
    """The most recent moment that falls on the previous local day."""
    today = clips_mod.local_date(NOW)
    when = NOW
    while clips_mod.local_date(when) == today:
        when -= 3600
    return when


class Arrivals(unittest.TestCase):
    def test_a_named_person_seen_today_is_announced_once(self):
        coord, client, hass = build({"77": "Alice"})
        poll(coord)      # priming poll, silent
        client.clips = [sighting(77, NOW - 60)]
        poll(coord)
        self.assertEqual([data["name"] for data in arrivals(hass)], ["Alice"])

    def test_further_sightings_the_same_day_are_not_announced(self):
        """The whole point: the twelfth crossing is noise, not an arrival."""
        coord, _, hass = build({"77": "Alice"})
        poll(coord)
        announce(coord, [sighting(77, NOW - 60)])
        announce(coord, [sighting(77, NOW - 60), sighting(77, NOW - 30)])
        announce(coord, [sighting(77, NOW - 30)])
        self.assertEqual(len(arrivals(hass)), 1)

    def test_an_unnamed_face_never_arrives(self):
        """A number arriving is a stranger appearing, which the detection
        event already reports. "Face 481036337152 has arrived" helps nobody."""
        coord, _, hass = build({})
        poll(coord)
        announce(coord, [sighting(481036337152, NOW - 60)])
        self.assertEqual(arrivals(hass), [])

    def test_a_restart_does_not_replay_the_morning(self):
        """The poll window holds a day of recordings. Without the priming
        guard, restarting at teatime announces everyone who came home at
        breakfast -- and the second poll is where that would happen, because
        the first has nothing published yet."""
        coord, client, hass = build({"77": "Alice"})
        client.clips = [sighting(77, earlier_today())]
        poll(coord)
        poll(coord)
        poll(coord)
        self.assertEqual(arrivals(hass), [])

    def test_a_new_day_re_arms(self):
        coord, _, hass = build({"77": "Alice"})
        poll(coord)
        announce(coord, [sighting(77, NOW - 60)])
        # Yesterday's record, which the day roll-over must discard.
        coord._arrival_day = clips_mod.local_date(NOW - DAY)
        announce(coord, [sighting(77, NOW - 60)])
        self.assertEqual(len(arrivals(hass)), 2)

    def test_a_sighting_from_yesterday_is_not_an_arrival_today(self):
        """The window reaches back a full day, so at one in the morning it
        still holds last night. Those are not arrivals for today."""
        coord, _, hass = build({"77": "Alice"})
        poll(coord)
        announce(coord, [sighting(77, yesterday())])
        self.assertEqual(arrivals(hass), [])

    def test_the_announcement_says_who_and_where(self):
        coord, client, hass = build({"77": "Alice"})
        poll(coord)
        client.clips = [sighting(77, NOW - 60)]
        poll(coord)
        data = arrivals(hass)[0]
        self.assertEqual(data["face_id"], "77")
        self.assertEqual(data["camera"], "Front")
        self.assertEqual(data["at"], NOW - 60)


class LocalDay(unittest.TestCase):
    def test_the_day_is_the_local_one_not_utc(self):
        """NOW is 2026-08-13T05:46:40Z, which in the harness's -07:00 zone is
        still the evening of the 12th. "Today" is a human word: someone home
        at half past ten at night has not arrived tomorrow."""
        self.assertEqual(clips_mod.local_date(NOW), "2026-08-12")

    def test_moments_an_hour_apart_share_a_day(self):
        self.assertEqual(clips_mod.local_date(NOW),
                         clips_mod.local_date(NOW - 3600))

    def test_moments_a_day_apart_do_not(self):
        self.assertNotEqual(clips_mod.local_date(NOW),
                            clips_mod.local_date(NOW - DAY))


class FirstSeen(unittest.TestCase):
    def test_it_is_the_oldest_sighting_in_the_window(self):
        coord, client, _ = build({"77": "Alice"})
        client.clips = [sighting(77, NOW - 60), sighting(77, NOW - 900)]
        poll(coord)
        face = coord.faces_seen()["77"]
        self.assertEqual(face["first_seen"], NOW - 900)
        self.assertEqual(face["last_seen"], NOW - 60)


class FreshData(unittest.TestCase):
    def test_faces_can_be_read_from_clips_not_yet_published(self):
        """The arrival check runs inside the poll that fetched the clips, so
        it has to be able to read them before the coordinator publishes."""
        coord, _, _ = build({})
        poll(coord)  # cameras have to be known before faces can be grouped
        self.assertEqual(coord.faces_seen(), {})
        fresh = coord.faces_seen(clips={0: [sighting(77, NOW - 60)]})
        self.assertEqual(sorted(fresh), ["77"])


if __name__ == "__main__":
    unittest.main()
