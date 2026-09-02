"""The three picture-shaped entities, constructed and asked.

camera.py, image.py and update.py were 156 lines at 0.0% coverage. Between
them they carry the exact defects this project has already paid for once: the
notification's Camera button showing the previous event, a frame from last
night reading as seconds old, and a dashboard never told to look again. Each
of those rules is held here by driving the real entity.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

camera_mod = importlib.import_module("tapo_h500.camera")
image_mod = importlib.import_module("tapo_h500.image")
update_mod = importlib.import_module("tapo_h500.update")
dt_util = sys.modules["homeassistant.util.dt"]

CAMERA = {"device_id": "cam0", "alias": "Front"}
NOW = int(dt_util.utcnow().timestamp())


def clip(start, length=15):
    return {"startTime": start, "endTime": start + length}


def _coordinator(clips=()):
    coord, _ = harness._build()
    coord.cameras = [CAMERA]
    coord.clips_for = lambda index: list(clips)
    coord.frames_asked = []

    async def latest_frame(index, camera):
        coord.frames_asked.append(index)
        return b"the newest frame"

    coord.frames_for = []

    async def frame_for(index, camera, start_time):
        coord.frames_for.append((index, start_time))
        return b"that event's own frame"

    coord.async_latest_frame = latest_frame
    coord.async_frame_for = frame_for
    return coord


def _wire(entity):
    """What Home Assistant supplies before the entity is used."""
    entity.hass = harness._Hass()
    entity.writes = 0

    def wrote():
        entity.writes += 1

    entity.async_write_ha_state = wrote
    entity.async_on_remove = lambda unsub: None
    return entity


def _connections(module):
    """Capture which signals a module's entities subscribe to."""
    seen = []
    original = module.async_dispatcher_connect

    def recording(hass, signal, target):
        seen.append((signal, target))
        return lambda: None

    module.async_dispatcher_connect = recording
    return seen, lambda: setattr(module, "async_dispatcher_connect", original)


class TheCameraEntity(unittest.TestCase):
    def _camera(self, coord=None):
        coord = coord or _coordinator()
        return _wire(camera_mod.H500Camera(coord, 0, CAMERA)), coord

    def test_the_picture_comes_through_the_coordinator(self):
        """A plain newest-on-disk scan here is how the notification's Camera
        button showed the previous event to everyone who pressed it."""
        entity, coord = self._camera()
        frame = asyncio.run(entity.async_camera_image())
        self.assertEqual(frame, b"the newest frame")
        self.assertEqual(coord.frames_asked, [0])

    def test_a_new_download_redraws_it(self):
        entity, _ = self._camera()
        entity._handle_new_image()
        self.assertEqual(entity.writes, 1)

    def test_it_listens_for_the_download_not_the_event(self):
        """The frame is written by the download. Redrawing on the event asks
        the frontend to fetch a picture that does not exist yet."""
        seen, restore = _connections(camera_mod)
        self.addCleanup(restore)
        entity, coord = self._camera()
        asyncio.run(entity.async_added_to_hass())
        self.assertEqual([signal for signal, _ in seen],
                         [coord.signal("image", 0)])

    def test_every_camera_gets_one(self):
        coord = _coordinator()
        coord.cameras = [CAMERA, {"device_id": "cam1", "alias": "Side"}]
        added = []
        hass = harness._Hass()
        coord.entry.runtime_data = coord

        hass.config_entries = harness._ConfigEntries([coord.entry])
        asyncio.run(camera_mod.async_setup_entry(
            hass, coord.entry, added.extend))
        self.assertEqual(len(added), 2)


class TheLatestEventPicture(unittest.TestCase):
    def _image(self, clips=()):
        coord = _coordinator(clips)
        entity = image_mod.H500EventImage(harness._Hass(), coord, 0, CAMERA)
        return _wire(entity), coord

    def test_the_stamp_is_when_the_frame_was_taken_not_when_we_looked(self):
        """utcnow() here was the whole reason a frame from last night read as
        seconds old."""
        entity, _ = self._image([clip(NOW - 7200)])
        entity._stamp()
        self.assertEqual(int(entity.image_last_updated.timestamp()),
                         NOW - 7200)

    def test_two_stamps_for_one_event_collapse_to_one_value(self):
        """The second stamp exists for the download landing after the fetch;
        it must not make the same frame look newly changed."""
        entity, _ = self._image([clip(NOW - 300)])
        entity._stamp()
        first = entity.image_last_updated
        entity._stamp()
        self.assertEqual(entity.image_last_updated, first)

    def test_a_camera_that_never_recorded_still_gets_a_timestamp(self):
        """There is no truer answer, and None would make the frontend never
        fetch at all."""
        entity, _ = self._image([])
        entity._stamp()
        self.assertIsNotNone(entity.image_last_updated)

    def test_an_event_stamps_it_and_a_download_stamps_it_again(self):
        seen, restore = _connections(image_mod)
        self.addCleanup(restore)
        entity, coord = self._image([clip(NOW - 60)])
        asyncio.run(entity.async_added_to_hass())
        self.assertEqual([signal for signal, _ in seen],
                         [coord.signal("event", 0),
                          coord.signal("image", 0)])
        for _, target in seen:
            try:
                target("motion", {})
            except TypeError:
                target()
        self.assertEqual(entity.writes, 2)

    def test_the_picture_itself_comes_through_the_coordinator(self):
        entity, coord = self._image([clip(NOW - 60)])
        self.assertEqual(asyncio.run(entity.async_image()),
                         b"the newest frame")

    def test_once_an_event_fires_the_picture_is_that_events_frame(self):
        """Not whatever clip is newest in the index, which a minute later is
        the next visitor. A notification's Image button opens this entity's
        dialog, so the picture has to be the event the notification named."""
        entity, coord = self._image([clip(NOW - 30)])
        entity._handle("motion", clip(NOW - 600))
        self.assertEqual(asyncio.run(entity.async_image()),
                         b"that event's own frame")
        self.assertEqual(coord.frames_for, [(0, NOW - 600)])
        self.assertEqual(coord.frames_asked, [], "the newest was not asked for")

    def test_the_stamp_and_the_age_follow_the_event_too(self):
        entity, _ = self._image([clip(NOW - 30)])
        entity._handle("motion", clip(NOW - 600))
        self.assertEqual(int(entity.image_last_updated.timestamp()), NOW - 600)
        self.assertGreaterEqual(entity.extra_state_attributes["frame_age_seconds"],
                                600)

    def test_an_event_without_a_start_time_does_not_unpin_the_last_one(self):
        entity, coord = self._image([clip(NOW - 30)])
        entity._handle("motion", clip(NOW - 600))
        entity._handle("motion", {})
        asyncio.run(entity.async_image())
        self.assertEqual(coord.frames_for, [(0, NOW - 600)])

    def test_the_next_event_moves_it_on(self):
        """One entity per camera: it shows the last event, and the last event
        changes. The notification for the earlier one then opens the later
        picture -- the honest limit of a per-camera entity."""
        entity, coord = self._image([])
        entity._handle("motion", clip(NOW - 600))
        entity._handle("ring", clip(NOW - 60))
        asyncio.run(entity.async_image())
        self.assertEqual(coord.frames_for, [(0, NOW - 60)])

    def test_the_age_is_spelled_out_for_a_person(self):
        """A wedged camera keeps serving its last frame forever, and a still
        picture cannot say so itself. The age is the signal."""
        entity, _ = self._image([clip(NOW - 3600)])
        attributes = entity.extra_state_attributes
        self.assertTrue(attributes["frame_taken"].startswith("20"))
        self.assertGreaterEqual(attributes["frame_age_seconds"], 3600)

    def test_a_clock_slightly_ahead_does_not_show_a_negative_age(self):
        entity, _ = self._image([clip(NOW + 30)])
        self.assertEqual(entity.extra_state_attributes["frame_age_seconds"], 0)

    def test_no_recordings_means_no_claimed_age(self):
        entity, _ = self._image([])
        self.assertEqual(entity.extra_state_attributes,
                         {"frame_taken": None, "frame_age_seconds": None})


class TheContactSheet(unittest.TestCase):
    def _sheet(self):
        coord = _coordinator()
        entity = image_mod.H500ContactSheet(harness._Hass(), coord, 0, CAMERA)
        return _wire(entity), coord

    def test_it_redraws_on_the_download_signal_only(self):
        """A sheet is built from thumbnails and a thumbnail is written by the
        download. Stamping on the event fetches an unchanged picture."""
        seen, restore = _connections(image_mod)
        self.addCleanup(restore)
        entity, coord = self._sheet()
        asyncio.run(entity.async_added_to_hass())
        self.assertEqual([signal for signal, _ in seen],
                         [coord.signal("image", 0)])

    def test_the_sheet_is_built_on_request_for_todays_local_date(self):
        asked = []
        original = image_mod.async_contact_sheet

        async def fake_sheet(hass, camera, day):
            asked.append((camera["device_id"], day))
            return b"sheet"

        image_mod.async_contact_sheet = fake_sheet
        self.addCleanup(setattr, image_mod, "async_contact_sheet", original)
        entity, _ = self._sheet()
        self.assertEqual(asyncio.run(entity.async_image()), b"sheet")
        device, day = asked[0]
        self.assertEqual(device, "cam0")
        expected = dt_util.as_local(dt_util.utcnow()).date().isoformat()
        self.assertEqual(day, expected,
                         "local, because 'today' is a human word")


class TheFirmwareEntity(unittest.TestCase):
    def _update(self, sw_version=None, firmware_info=None):
        coord, _ = harness._build()
        coord.client = harness._Client()
        coord.client.info = {"device_model": "H500"}
        if sw_version:
            coord.client.info["sw_version"] = sw_version
        coord.firmware_info = firmware_info or {}
        return update_mod.H500FirmwareUpdate(coord, harness._Entry(20)), coord

    def test_the_build_tail_is_trimmed_from_the_installed_version(self):
        entity, _ = self._update("1.3.20 Build 20260605 rel.62028")
        self.assertEqual(entity.installed_version, "1.3.20")

    def test_an_empty_cloud_answer_means_up_to_date_not_unknown(self):
        """A WAN-blocked hub keeps an empty upgrade block forever. Unknown
        would show as a permanent pending update on an offline hub."""
        entity, _ = self._update("1.3.20 Build 20260605 rel.62028")
        self.assertEqual(entity.latest_version, "1.3.20")

    def test_a_pending_update_shows_its_version(self):
        entity, _ = self._update("1.3.20 Build 20260605",
                                 {"version": "1.3.21", "raw": {"v": "1.3.21"}})
        self.assertEqual(entity.latest_version, "1.3.21")
        self.assertEqual(entity.extra_state_attributes,
                         {"upgrade_info": {"v": "1.3.21"}})

    def test_a_hub_that_never_said_its_version_shows_none(self):
        entity, _ = self._update(None)
        self.assertIsNone(entity.installed_version)
        self.assertIsNone(entity.latest_version)

    def test_the_raw_block_rides_along_even_when_empty(self):
        """A pending update whose field names this integration has never
        seen must still be visible somewhere."""
        entity, _ = self._update("1.3.20")
        self.assertEqual(entity.extra_state_attributes, {"upgrade_info": {}})


if __name__ == "__main__":
    unittest.main()
