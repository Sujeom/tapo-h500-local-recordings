"""Working out the camera layout instead of asking for it.

The layout screen calls this the one thing the integration cannot work out for
itself. That was true of the hub, which reports no geometry, and untrue of the
recordings: people arrive from the street and walk towards the door, so the
camera that sees somebody FIRST is the one nearer the street. The trails have
carried that all along and nothing read them.

It stays a suggestion. It fills in the form's defaults and the owner still
presses submit -- a guessed direction is worse than none, because "someone is
approaching the door" is what people wire a siren to.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
FLOW = (COMPONENT / "config_flow.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

coordinator_mod = importlib.import_module("tapo_h500.coordinator")
clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000
WINDOW = const.DIRECTION_WINDOW


def trail(*hops):
    """Newest first, the way faces_seen builds one."""
    return [{"camera": camera, "at": at} for camera, at in hops]


def journey(*cameras, gap=30, start=NOW - 600):
    """One person walking past these cameras in this order."""
    return trail(*[(camera, start + step * gap)
                   for step, camera in enumerate(cameras)][::-1])


class Inferring(unittest.TestCase):
    def test_the_camera_seen_first_is_nearest_the_street(self):
        ranks = clips.suggest_ranks([journey("Gate", "Door")], WINDOW)
        self.assertEqual(ranks, {"Gate": 0, "Door": 1})

    def test_the_trail_is_read_newest_first(self):
        """faces_seen builds them that way. Reading them in arrival order
        counts every journey as going the other way, which produces a
        confidently reversed layout rather than an obviously broken one."""
        walk = journey("Gate", "Door")
        self.assertGreater(walk[0]["at"], walk[-1]["at"])
        self.assertLess(clips.suggest_ranks([walk], WINDOW)["Gate"],
                        clips.suggest_ranks([walk], WINDOW)["Door"])

    def test_three_cameras_come_out_in_order(self):
        walk = journey("Street", "Path", "Porch")
        self.assertEqual(clips.suggest_ranks([walk], WINDOW),
                         {"Street": 0, "Path": 1, "Porch": 2})

    def test_the_majority_of_journeys_decides(self):
        """One person leaving does not reverse the house."""
        walks = [journey("Gate", "Door"), journey("Gate", "Door"),
                 journey("Door", "Gate")]
        self.assertEqual(clips.suggest_ranks(walks, WINDOW),
                         {"Gate": 0, "Door": 1})

    def test_nothing_is_suggested_without_evidence(self):
        """A guess with nothing behind it is worse than no guess: this becomes
        the default on a form."""
        self.assertEqual(clips.suggest_ranks([], WINDOW), {})
        self.assertEqual(clips.suggest_ranks([trail(("Gate", NOW))], WINDOW), {})

    def test_standing_still_is_not_a_journey(self):
        """Two clips at the same camera say nothing about which is nearer."""
        same = trail(("Gate", NOW), ("Gate", NOW - 30))
        self.assertEqual(clips.suggest_ranks([same], WINDOW), {})

    def test_sightings_too_far_apart_are_two_visits(self):
        """Somebody at the gate this morning and the door this evening did not
        walk from one to the other."""
        apart = journey("Gate", "Door", gap=WINDOW * 4)
        self.assertEqual(clips.suggest_ranks([apart], WINDOW), {})

    def test_a_camera_nobody_crossed_is_left_out(self):
        """No evidence about it, so no opinion -- rather than defaulting it to
        the street and inventing a direction through it."""
        ranks = clips.suggest_ranks([journey("Gate", "Door")], WINDOW)
        self.assertNotIn("Shed", ranks)

    def test_the_answer_does_not_depend_on_dictionary_ordering(self):
        """Two cameras with the same score must still come out the same way
        every time, or the suggestion changes on each visit to the form."""
        both = [journey("A", "B"), journey("B", "A")]
        self.assertEqual(clips.suggest_ranks(both, WINDOW),
                         clips.suggest_ranks(both[::-1], WINDOW))

    def test_the_ranks_it_produces_actually_yield_a_direction(self):
        """The whole point of the number. A layout that produces None from the
        trail it was inferred from would be worse than useless."""
        walk = journey("Gate", "Door", gap=20)
        ranks = clips.suggest_ranks([walk], WINDOW)
        self.assertEqual(clips.direction(walk, ranks, WINDOW), "approaching")


class FromRecordings(unittest.TestCase):
    """Driven through the coordinator, so the trails are the real ones."""

    CAMERAS = [{"device_id": "cam0", "alias": "Gate"},
               {"device_id": "cam1", "alias": "Door"}]

    def _build(self, per_camera, names=None):
        cameras = self.CAMERAS

        class _Client:
            def cameras(self):
                return list(cameras)

            def recent(self, camera, start, end):
                return list(per_camera.get(camera["alias"], []))

            detections = recent

            def hub_status(self):
                return {}

        coord = coordinator_mod.H500Coordinator(
            harness._Hass(), harness._Entry(20, face_names=names or {}),
            _Client())
        coord._download_new = lambda *a, **k: None
        coord.data = asyncio.run(coord._async_update_data())
        return coord

    @staticmethod
    def _sighting(face_id, when):
        return {"startTime": when, "endTime": when + 10,
                "events_1": 1 << (20 - 1),
                "event_info": [{"face_id": face_id}]}

    def test_it_reads_real_sightings(self):
        coord = self._build({
            "Gate": [self._sighting(77, NOW - 120)],
            "Door": [self._sighting(77, NOW - 90)],
        })
        self.assertEqual(coord.suggested_ranks(), {"Gate": 0, "Door": 1})

    def test_an_unnamed_face_counts_too(self):
        """Most people who walk up to a door are never named, and their
        journeys are the same evidence."""
        coord = self._build({
            "Gate": [self._sighting(481036337152, NOW - 120)],
            "Door": [self._sighting(481036337152, NOW - 90)],
        })
        self.assertEqual(coord.suggested_ranks(), {"Gate": 0, "Door": 1})

    def test_a_person_clustered_twice_still_makes_one_journey(self):
        """Gate under one cluster and door under another. Unmerged, neither
        half is a journey and the layout can never be inferred."""
        coord = self._build({
            "Gate": [self._sighting(11, NOW - 120)],
            "Door": [self._sighting(22, NOW - 90)],
        }, names={"11": "Alice", "22": "Alice"})
        self.assertEqual(coord.suggested_ranks(), {"Gate": 0, "Door": 1})

    def test_nothing_seen_suggests_nothing(self):
        self.assertEqual(self._build({}).suggested_ranks(), {})


class Form(unittest.TestCase):
    def test_a_saved_answer_wins_over_the_suggestion(self):
        """Overwriting a deliberate answer on every visit to this screen would
        silently undo it."""
        body = FLOW.split("async def async_step_layout", 1)[1] \
                   .split("def _layout_note", 1)[0]
        self.assertIn("ranks.get(name, suggested.get(name, 0))", body)

    def test_nothing_is_stored_until_the_form_is_submitted(self):
        """It fills in defaults; it does not decide. The only write is behind
        the user_input guard."""
        body = FLOW.split("async def async_step_layout", 1)[1] \
                   .split("def _layout_note", 1)[0]
        before, after = body.split("if user_input is not None:", 1)
        self.assertNotIn("async_create_entry", before)
        self.assertIn("async_create_entry", after)
        self.assertNotIn("async_update_entry", body)

    def test_the_form_says_the_numbers_were_inferred(self):
        """Prefilled numbers that arrived from nowhere read as settings
        somebody else chose."""
        self.assertIn("description_placeholders", FLOW)
        note = FLOW.split("def _layout_note", 1)[1]
        self.assertIn("Suggested from how people have actually moved", note)

    def test_it_says_nothing_when_it_inferred_nothing(self):
        self.assertEqual(_note(["Gate", "Door"], {}, {}), "")

    def test_the_note_lists_them_street_first(self):
        """Alphabetically would read "Door then Gate", which is the layout
        backwards and describes the opposite house."""
        note = _note(["Gate", "Door"], {}, {"Gate": 0, "Door": 1})
        self.assertLess(note.index("Gate"), note.index("Door"))

    def test_it_does_not_claim_credit_for_answers_you_gave(self):
        self.assertEqual(_note(["Gate"], {"Gate": 3}, {"Gate": 0}), "")


def _note(names, ranks, suggested) -> str:
    """The form's note, pulled out of the source so it can be run.

    config_flow.py imports the Home Assistant selector helpers, which are not
    available here, so the one static method is compiled on its own rather
    than importing the module.
    """
    body = FLOW.split("    @staticmethod\n    def _layout_note", 1)[1]
    body = body.split("\n    async def", 1)[0]
    source = "def _layout_note" + body
    source = "\n".join(line[4:] if line.startswith("    ") else line
                       for line in source.splitlines())
    namespace: dict = {}
    exec(compile(source, "config_flow.py", "exec"), namespace)  # noqa: S102
    return namespace["_layout_note"](names, ranks, suggested)


if __name__ == "__main__":
    unittest.main()
