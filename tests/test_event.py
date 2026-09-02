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
        # Built once in _handle and shared with image_link, so the dict
        # carries the name and the call sits just above it.
        self.assertRegex(EVENT, r"image = self\._own_frame\(start_time\)")
        self.assertRegex(EVENT, r'"image":\s*image,')

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



class TheTwoPictureUrls(unittest.TestCase):
    """`image` is the downloaded file; `preview` is one fetched on demand.

    The distinction is the whole reason both exist. `image` addresses a path
    and does not check it, so it 404s for every clip that was never
    downloaded and for every clip at all until the hub stops recording -- a
    notification using it shows an empty picture some of the time, with
    nothing to tell that apart from one that has not arrived yet.
    """

    def _fired(self, index=1, **entry):
        coord, _ = harness._build()
        entity = event_mod.H500ActivityEvent(coord, index, CAMERA)
        entity.hass = harness._Hass()
        # `image` is a path under the media root, so there has to be one.
        entity.hass.config = type("C", (), {
            "media_dirs": {"local": "/media"}})()
        entity.entity_id = "event.front_activity"
        entity._handle("motion", {"events_1": 1 << 1,
                                  **({"startTime": NOW, "endTime": NOW + 15}
                                     if not entry else entry)})
        return entity.triggered[-1][1]

    def test_both_are_offered(self):
        attributes = self._fired()
        self.assertTrue(attributes["image"])
        self.assertTrue(attributes["preview"])

    def test_they_are_not_the_same_url(self):
        attributes = self._fired()
        self.assertNotEqual(attributes["image"], attributes["preview"])

    def test_the_preview_goes_to_the_endpoint_that_generates_one(self):
        self.assertIn("/api/tapo_h500/preview/", self._fired()["preview"])

    def test_it_names_this_event_rather_than_the_newest(self):
        """Pinned to this clip's own moment: asked two minutes later, a URL
        for "the newest" answers with the wrong picture."""
        self.assertIn(str(NOW), self._fired()["preview"])

    def test_it_carries_the_camera_this_event_belongs_to(self):
        self.assertIn("/1/", self._fired()["preview"],
                      "camera 1's preview, not camera 0's")

    def test_an_event_with_no_start_time_offers_neither(self):
        attributes = self._fired(index=0, events_1=1 << 1)
        self.assertIsNone(attributes["image"])
        self.assertIsNone(attributes["preview"])


class TheVideoUrl(TheTwoPictureUrls):
    """`video` is this event's own recording, beside `image`, its still.

    It addresses a downloaded file and there is no on-demand fallback: the
    hub will produce a frame for a clip nobody kept, but not the clip itself.
    """

    def test_it_is_the_recording_beside_the_still(self):
        fired = self._fired()
        self.assertEqual(fired["video"].replace(".mp4", ".jpg"), fired["image"])

    def test_it_is_signed(self):
        """A notification action opens in a webview with no session, so the
        signature is the only credential there is."""
        self.assertIn("authSig", self._fired()["video"])

    def test_an_event_with_no_start_time_has_none(self):
        """There is no file to name without a moment to name it from."""
        self.assertIsNone(self._fired(index=0, events_1=1 << 1)["video"])

    def test_it_addresses_the_file_the_download_actually_writes(self):
        """The invariant worth holding. `clip_path` decides where the clip
        lands and `_video` decides what the button opens; if they ever
        disagree the phone gets a 404 for a recording that is right there.
        """
        from custom_components.tapo_h500.media import clip_path
        coord, _ = harness._build()
        entity = event_mod.H500ActivityEvent(coord, 1, CAMERA)
        entity.hass = harness._Hass()
        entity.hass.config = type("C", (), {
            "media_dirs": {"local": "/media"}})()
        entity.entity_id = "event.front_activity"
        entity._handle("motion", {"events_1": 1 << 1,
                                  "startTime": NOW, "endTime": NOW + 15})
        video = entity.triggered[-1][1]["video"].split("?")[0]
        written = clip_path(entity.hass, CAMERA, NOW, ".mp4")
        self.assertTrue(
            video.endswith(f"/{written.parent.parent.name}"
                           f"/{written.parent.name}/{written.name}"),
            f"{video} does not open {written}")


class TheLinksAButtonCanOpen(TheTwoPictureUrls):
    """`image_link` and `video_link`: the signed paths as absolute URLs.

    The Android companion app routes a notification button by the shape of
    its URI. A relative path is loaded in the app's own webview, and the app
    appends `external_auth=1` to it on the way in -- which the frontend
    needs, and which turns a signed media path into a 401, because Home
    Assistant rejects any query parameter it did not sign. An absolute URL is
    handed to the system browser untouched. Read from the app's own source
    after three relative-path attempts had each answered 401 from a phone.
    """

    def test_they_are_absolute(self):
        fired = self._fired()
        for key in ("image_link", "video_link"):
            self.assertTrue(fired[key].startswith("http"), fired[key])

    def test_they_carry_the_signed_path_unchanged(self):
        """The browser sends exactly the query that was signed; so must this."""
        fired = self._fired()
        self.assertTrue(fired["image_link"].endswith(fired["preview"]))
        self.assertTrue(fired["video_link"].endswith(fired["video"]))

    def test_the_image_prefers_the_on_demand_frame(self):
        """`preview` renders for a clip that was never downloaded; `image`
        404s until one is. A button should open the one that works."""
        fired = self._fired()
        self.assertIn("/api/tapo_h500/preview/", fired["image_link"])

    def test_an_event_with_no_start_time_has_neither(self):
        attributes = self._fired(index=0, events_1=1 << 1)
        self.assertIsNone(attributes["image_link"])
        self.assertIsNone(attributes["video_link"])


class SignedForSomethingWithNoSession(unittest.TestCase):
    """The signing call names the content user rather than relying on it.

    Every URL here is minted in a background poll with no request and no
    websocket connection behind it. Core already handles that: with nothing
    else to bind the signature to, `async_sign_path` signs as the content
    user, the account it keeps for media that something will fetch later on
    its own. Saying so in the call makes the intent visible and survives a
    future core deciding to raise instead of falling back.
    """

    def _sign(self):
        from homeassistant.components.http import auth
        from custom_components.tapo_h500.media import sign
        auth.signed_as_content_user.clear()
        url = sign(harness._Hass(), "/media/local/x.jpg")
        return url, auth.signed_as_content_user

    def test_it_signs_as_the_content_user(self):
        """The account Home Assistant keeps for media handed to something
        that will fetch it later on its own. Cast signs this way too."""
        _, asked = self._sign()
        self.assertEqual(asked, [True])

    def test_it_still_signs_on_a_core_that_cannot(self):
        """Older cores do not take the argument, and signing without it is
        what this did before -- better than not signing at all."""
        from custom_components.tapo_h500 import media as media_mod
        original = media_mod.async_sign_path
        try:
            # Rebound where media.py holds it: `from ... import` copies the
            # name, so patching the auth module would change nothing.
            media_mod.async_sign_path = (
                lambda hass, path, expiry: f"{path}?authSig=old")
            self.assertEqual(media_mod.sign(harness._Hass(), "/media/local/x.jpg"),
                             "/media/local/x.jpg?authSig=old")
        finally:
            media_mod.async_sign_path = original


if __name__ == "__main__":
    unittest.main()
