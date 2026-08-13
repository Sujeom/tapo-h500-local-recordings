"""The event carries a frame pinned to its own timestamp.

Checked statically: event.py imports the Home Assistant event platform, which
is not installed here. What matters is small and greppable -- that the image
comes from the event's start time rather than from whatever thumbnail happens
to be newest, and that the example automation uses it.

The distinction is the whole point. A camera entity answering with the newest
thumbnail is correct for a camera; asking it two minutes after an event, once
another clip has landed, gives the wrong picture for that event.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
EVENT = (ROOT / "custom_components" / "tapo_h500" / "event.py").read_text()
AUTOMATION = (ROOT / "examples" / "notify-person-pet-doorbell.yaml").read_text()


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


if __name__ == "__main__":
    unittest.main()
