"""The four Configure screens, driven as flows rather than read as text.

The reconfigure and reauth steps got behavioural tests when they were built;
the options flow -- settings, faces, layout, sensitivity -- stayed at string
matching. These construct the real flow, submit the real forms, and read back
what would be saved.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)
import test_api  # noqa: E402,F401  (installs the pytapo stubs)

config_flow = importlib.import_module("tapo_h500.config_flow")
NOW = 1_786_600_000


def clip(start, face=None):
    made = {"startTime": start, "endTime": start + 15, "events_1": 1 << 5}
    if face is not None:
        made["event_info"] = [{"face_id": face}]
    return made


def run(coro):
    return asyncio.run(coro)


class OneBoundForBothForms(unittest.TestCase):
    """The poll interval appears on the setup form and on the Configure page,
    and the two must agree about what a valid interval is.

    Two copies drifted apart once: the floor ended up above the default, so
    the default could not be saved. Identity, not a matching pair of literals
    -- there is deliberately one object, and this is what says so.
    """

    def _fields(self, result):
        return {str(key): key for key in result["data_schema"].schema}

    def test_the_interval_is_offered_while_adding_the_hub(self):
        """It decides whether notifications feel instant, so it should not be
        reachable only after the fact."""
        flow = config_flow.TapoH500ConfigFlow()
        flow.hass = harness._Hass()
        setup = self._fields(run(flow.async_step_user(None)))
        self.assertIn("poll_interval", setup)

    def test_both_forms_validate_it_with_the_same_object(self):
        flow = config_flow.TapoH500ConfigFlow()
        flow.hass = harness._Hass()
        setup = run(flow.async_step_user(None))["data_schema"].schema

        coord, _ = harness._build()
        options_flow = config_flow.TapoH500OptionsFlow()
        options_flow.hass = harness._Hass()
        options_flow.config_entry = coord.entry
        settings = run(options_flow.async_step_settings(None))[
            "data_schema"].schema

        def bound(schema):
            return next(value for key, value in schema.items()
                        if str(key) == "poll_interval")

        self.assertIs(bound(setup), bound(settings))

    def test_both_start_at_the_same_default(self):
        flow = config_flow.TapoH500ConfigFlow()
        flow.hass = harness._Hass()
        setup = self._fields(run(flow.async_step_user(None)))
        coord, _ = harness._build()
        coord.entry.options = {}
        options_flow = config_flow.TapoH500OptionsFlow()
        options_flow.hass = harness._Hass()
        options_flow.config_entry = coord.entry
        settings = self._fields(run(options_flow.async_step_settings(None)))
        self.assertEqual(setup["poll_interval"].default(),
                         settings["poll_interval"].default())


class _World(unittest.TestCase):
    def setUp(self):
        self.coord, self.client = harness._build()
        self.coord.cameras = [{"device_id": "cam0", "alias": "Front"},
                              {"device_id": "cam1", "alias": "Side"}]
        self.hass = harness._Hass()
        self.hass.data = {"tapo_h500": {"hubs": {"test": self.coord}}}
        self.flow = config_flow.TapoH500OptionsFlow()
        self.flow.hass = self.hass
        self.flow.config_entry = self.coord.entry

    def _options(self, **options):
        self.coord.entry.options = {**self.coord.entry.options, **options}

    def _defaults(self, result):
        return {str(key): key.default() for key in result["data_schema"].schema
                if getattr(key, "default", None) is not None}


class TheMenu(_World):
    def test_it_offers_the_four_screens(self):
        result = run(self.flow.async_step_init())
        self.assertEqual(result["menu_options"],
                         ["settings", "faces", "layout", "sensitivity"])


class TheSettingsScreen(_World):
    def test_saving_carries_what_the_form_does_not_ask_about(self):
        """Options are replaced wholesale on save. Face names appear on no
        settings form, and saving this screen used to delete every one of
        them without a word."""
        self._options(face_names={"7": "Sam"})
        result = run(self.flow.async_step_settings({"poll_interval": 5}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["face_names"], {"7": "Sam"})
        self.assertEqual(result["data"]["poll_interval"], 5)

    def test_the_form_starts_at_the_stored_values(self):
        self._options(poll_interval=7, card_days=3)
        defaults = self._defaults(run(self.flow.async_step_settings()))
        self.assertEqual(defaults["poll_interval"], 7)
        self.assertEqual(defaults["card_days"], 3)

    def test_the_silence_ceiling_is_the_poll_window(self):
        """The hub is asked for a day of recordings; "nothing in three days"
        is not a question it can answer, and offering 72 makes a sensor that
        never turns on for a reason nobody can see."""
        source = (Path(__file__).parents[1] / "custom_components" /
                  "tapo_h500" / "config_flow.py").read_text()
        self.assertIn("max=LOOKBACK_SECONDS // 3600", source)


class TheFacesScreen(_World):
    def _seen(self, *ids, named=None):
        self.coord.data = {"clips": {0: [clip(NOW - 60 - n, face=f)
                                         for n, f in enumerate(ids)]}}
        if named:
            self._options(face_names=named)

    def test_every_face_seen_gets_a_box_and_a_line(self):
        self._seen(111, 222)
        result = run(self.flow.async_step_faces())
        self.assertEqual(result["type"], "form")
        self.assertEqual(sorted(str(k) for k in result["data_schema"].schema),
                         ["111", "222"])
        described = result["description_placeholders"]["faces"]
        self.assertIn("111", described)
        self.assertIn("Front", described, "where they were seen")

    def test_someone_named_but_quiet_stays_editable(self):
        """Or a name could only ever be added, never corrected."""
        self._seen(111, named={"999": "Sam"})
        result = run(self.flow.async_step_faces())
        self.assertIn("999", {str(k) for k in result["data_schema"].schema})
        self.assertIn("not seen recently",
                      result["description_placeholders"]["faces"])

    def test_no_faces_at_all_is_an_explained_abort(self):
        self.coord.data = {"clips": {0: []}}
        result = run(self.flow.async_step_faces())
        self.assertEqual(result["reason"], "no_faces")

    def test_saving_names_and_clearing_boxes(self):
        self._seen(111, named={"222": "Old"})
        result = run(self.flow.async_step_faces(
            {"111": "  Alice ", "222": ""}))
        self.assertEqual(result["data"]["face_names"], {"111": "Alice"})

    def test_saving_names_does_not_wipe_the_other_options(self):
        self._seen(111)
        self._options(keep_downloads=25)
        result = run(self.flow.async_step_faces({"111": "Alice"}))
        self.assertEqual(result["data"]["keep_downloads"], 25)


class TheFacePhotograph(_World):
    def _face(self, **extra):
        return {"camera_index": 0, "last_seen": NOW - 60,
                "sightings": 3, "cameras": ["Front"], **extra}

    def _url(self, face, exists=True, origin="http://ha.local:8123"):
        thumb = Path(__file__).parent / "_flow_thumb.jpg"
        if exists:
            thumb.write_bytes(b"jpeg")
            self.addCleanup(thumb.unlink)
        for name, value in (
            ("clip_path", lambda hass, camera, moment, suffix: thumb),
            ("signed_url", lambda hass, path: "/media/x.jpg?authSig=s"),
        ):
            self.addCleanup(setattr, config_flow, name,
                            getattr(config_flow, name))
            setattr(config_flow, name, value)
        self.addCleanup(setattr, config_flow, "get_url",
                        getattr(config_flow, "get_url"))
        if origin is None:
            def no_url(hass):
                raise config_flow.NoURLAvailableError()
            config_flow.get_url = no_url
        else:
            config_flow.get_url = lambda hass: origin + "/"
        return self.flow._photo_url(face, self.coord)

    def test_a_downloaded_sighting_links_its_own_photo_absolutely(self):
        """Markdown links go through the frontend's router, which treats a
        root-relative path as an in-app route and goes nowhere."""
        self.assertEqual(self._url(self._face()),
                         "http://ha.local:8123/media/x.jpg?authSig=s")

    def test_no_downloaded_clip_means_no_link_rather_than_a_dead_one(self):
        self.assertIsNone(self._url(self._face(), exists=False))

    def test_a_face_never_placed_gets_no_link(self):
        self.assertIsNone(self._url({"sightings": 1}))

    def test_no_configured_url_still_offers_the_relative_form(self):
        self.assertEqual(self._url(self._face(), origin=None),
                         "/media/x.jpg?authSig=s")


class TheLayoutScreen(_World):
    def test_one_camera_cannot_have_a_direction(self):
        self.coord.cameras = [{"device_id": "cam0", "alias": "Front"}]
        result = run(self.flow.async_step_layout())
        self.assertEqual(result["reason"], "one_camera")

    def test_the_suggestion_fills_the_defaults_and_says_so(self):
        self.coord.suggested_ranks = lambda: {"Front": 0, "Side": 1}
        result = run(self.flow.async_step_layout())
        self.assertEqual(self._defaults(result), {"Front": 0, "Side": 1})
        note = result["description_placeholders"]["suggestion"]
        self.assertIn("Front → Side", note)
        self.assertIn("nothing is stored until you submit", note)

    def test_a_saved_answer_beats_the_suggestion_silently_and_wordlessly(self):
        """Overwriting a deliberate answer with an inferred one on every
        visit would silently undo it."""
        self._options(camera_order={"Front": 2})
        self.coord.suggested_ranks = lambda: {"Front": 0, "Side": 1}
        result = run(self.flow.async_step_layout())
        self.assertEqual(self._defaults(result)["Front"], 2)

    def test_nothing_to_suggest_means_no_note(self):
        self._options(camera_order={"Front": 0, "Side": 1})
        self.coord.suggested_ranks = lambda: {"Front": 0, "Side": 1}
        result = run(self.flow.async_step_layout())
        self.assertEqual(result["description_placeholders"]["suggestion"], "")

    def test_saving_stores_integers_and_keeps_the_rest(self):
        self._options(face_names={"7": "Sam"})
        result = run(self.flow.async_step_layout({"Front": "0", "Side": 2}))
        self.assertEqual(result["data"]["camera_order"],
                         {"Front": 0, "Side": 2})
        self.assertEqual(result["data"]["face_names"], {"7": "Sam"})


class TheSensitivityScreen(_World):
    def test_each_camera_gets_a_level_at_its_stored_default(self):
        self._options(sensitivity={"Front": "relaxed"})
        result = run(self.flow.async_step_sensitivity())
        defaults = self._defaults(result)
        self.assertEqual(defaults["Front"], "relaxed")
        self.assertEqual(defaults["Side"], "normal")

    def test_saving_updates_without_dropping_the_other_camera(self):
        self._options(sensitivity={"Side": "sensitive"})
        result = run(self.flow.async_step_sensitivity({"Front": "relaxed"}))
        self.assertEqual(result["data"]["sensitivity"],
                         {"Front": "relaxed", "Side": "sensitive"})

    def test_no_cameras_is_an_explained_abort(self):
        self.coord.cameras = []
        result = run(self.flow.async_step_sensitivity())
        self.assertEqual(result["reason"], "no_cameras")


if __name__ == "__main__":
    unittest.main()
