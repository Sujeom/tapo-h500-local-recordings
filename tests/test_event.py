"""The event carries a frame pinned to its own timestamp, and says if it was muted.

The frame distinction is the whole point of `_own_frame`. A camera entity
answering with the newest thumbnail is correct for a camera; asking it two
minutes after an event, once another clip has landed, gives the wrong picture
for that event.

The muting matters for a different reason: "there was activity and I got no
message" has two answers on this side, and only one of them used to be
visible after the fact.
"""
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
EVENT = (ROOT / "custom_components" / "tapo_h500" / "event.py").read_text()
AUTOMATION = (ROOT / "examples" / "notify-person-pet-doorbell.yaml").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

event_mod = importlib.import_module("tapo_h500.event")

CAMERA = {"device_id": "cam0", "alias": "Front"}
NOW = 1_786_600_000


class OwnFrame(unittest.TestCase):
    def test_the_frame_is_derived_from_the_events_start_time(self):
        """clip_path maps a start time to that clip's own .jpg."""
        self.assertRegex(
            EVENT, r"clip_path\(\s*self\.hass,\s*self\.camera,\s*start_time,\s*\"\.jpg\"\)")
        self.assertRegex(EVENT, r'"image":\s*self\._own_frame\(start_time\)')

    def test_a_missing_timestamp_yields_no_url_rather_than_a_wrong_one(self):
        body = EVENT.split("def _own_frame", 1)[1].split("@callback", 1)[0]
        self.assertIn("if start_time is None:", body)
        self.assertIn("return None", body)

    def test_it_does_not_fall_back_to_the_newest_thumbnail(self):
        """The bug being prevented: any use of the live camera image here."""
        self.assertNotIn("camera_proxy", EVENT)
        self.assertNotIn("_newest_thumbnail", EVENT)
        self.assertNotIn("async_latest_image", EVENT)

    def test_building_the_url_never_fails_the_event(self):
        """An attribute is not worth losing a doorbell notification over."""
        body = EVENT.split("def _own_frame", 1)[1].split("@callback", 1)[0]
        self.assertIn("except Exception", body)


class AutomationUsesIt(unittest.TestCase):
    def test_the_notification_sends_the_events_own_frame(self):
        self.assertIn("state_attr(trigger.entity_id, 'image')", AUTOMATION)
        self.assertIn('image: "{{ frame }}"', AUTOMATION)

    def test_the_notification_no_longer_sends_the_live_camera(self):
        self.assertNotIn("image: /api/camera_proxy", AUTOMATION)


class WhyThereWasNoNotification(unittest.TestCase):
    """"There was activity and I got no message" has two answers on this
    side, and neither used to be visible after the fact.

    Either no event fired, which the event entity's own recorded history
    answers, or one fired while notifications were snoozed, which nothing
    recorded at all. That question went to the automation traces, which are
    kept for a few runs and not at all across a restart -- so by the time
    anybody asked, the answer was gone.
    """

    def _fire(self, kind="motion", snoozed_until=None):
        coord, _ = harness._build()
        coord.snoozed_until = snoozed_until
        entity = event_mod.H500ActivityEvent(coord, 0, CAMERA)
        entity.hass = harness._Hass()
        entity.entity_id = "event.front_activity"
        entity._handle(kind, {"startTime": NOW, "endTime": NOW + 15,
                              "alarm_type": "2"})
        return entity

    def test_an_event_says_it_was_not_muted(self):
        _, attributes = self._fire().triggered[0]
        self.assertIs(attributes["snoozed"], False)

    def test_an_event_fired_during_a_snooze_says_so(self):
        entity = self._fire(snoozed_until=NOW + 3600)
        self.assertIs(entity.triggered[0][1]["snoozed"], True)

    def test_it_is_recorded_on_the_event_not_read_back_later(self):
        """A snooze that has since expired is exactly the case that needs
        explaining, and reading the switch afterwards would say "not
        snoozed" about an event that was."""
        entity = self._fire(snoozed_until=NOW + 3600)
        entity.coordinator.snoozed_until = None
        self.assertIs(entity.triggered[0][1]["snoozed"], True)

    def test_the_bus_payload_carries_it_too(self):
        """An automation triggering on the bus should not have to go and
        read a switch to learn it."""
        entity = self._fire(snoozed_until=NOW + 3600)
        name, payload = entity.hass.bus.fired[0]
        self.assertEqual(name, "tapo_h500_event")
        self.assertIs(payload["snoozed"], True)

    def test_recording_carries_on_regardless(self):
        """A snooze mutes the automation and nothing else. Footage during a
        snooze is the footage most likely to be wanted afterwards."""
        entity = self._fire(kind="ring", snoozed_until=NOW + 3600)
        self.assertEqual(entity.triggered[0][0], "ring")
        self.assertEqual(entity.triggered[0][1]["camera_index"], 0)


if __name__ == "__main__":
    unittest.main()
