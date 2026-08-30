"""Captioning, the digest, ring-protected retention and the voice answers.

The summariser and the retention filter are pure and are run for real. The
services and intents are checked statically. The theme across all four is
restraint: nothing leaves the house unless asked, no digest arrives unrequested,
and a spoken answer uses the same words as a written one.
"""
import importlib
import sys
import types
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
INIT = (COMPONENT / "__init__.py").read_text()
# The thirteen service handlers moved out of the package body.
SERVICES_SRC = (COMPONENT / "services.py").read_text()
MEDIA = (COMPONENT / "media.py").read_text()
INTENT = (COMPONENT / "intent.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator  # noqa: E402,F401  (installs the HA stubs)

package = types.ModuleType("tapo_h500")
package.__path__ = [str(COMPONENT)]
sys.modules.setdefault("tapo_h500", package)
clips = importlib.import_module("tapo_h500.clips")
const = importlib.import_module("tapo_h500.const")

NOW = 1_786_600_000


def clip(offset, mask):
    return {"startTime": NOW - offset, "events_1": mask}


def mask(*codes):
    """events_1 for these alarm codes. Code N is bit N-1, and writing the
    literals by hand got that wrong -- 0b1_0000_0110_0010 looks like a press
    and is actually codes 2, 6, 7 and 13."""
    value = 0
    for code in codes:
        value |= 1 << (code - 1)
    return value


PERSON = mask(2, 6)
PRESS = mask(2, 6, 10, 17)
MOTION = mask(2)


class Summary(unittest.TestCase):
    def test_it_counts_what_mattered_per_camera(self):
        text = clips.summarise({"Front": [clip(60, PERSON), clip(120, PERSON)]}, NOW)
        self.assertIn("Front: 2 recordings", text)
        self.assertIn("2 person", text)

    def test_motion_does_not_dominate_the_summary(self):
        """It accompanies nearly everything, so counting it would make every
        day read the same."""
        text = clips.summarise({"Front": [clip(60, PERSON)]}, NOW)
        self.assertNotIn("motion", text)

    def test_a_press_is_not_counted_twice(self):
        """10 rides along with every 17."""
        text = clips.summarise({"Front": [clip(60, PRESS)]}, NOW)
        self.assertEqual(text.count("doorbell"), 1)
        self.assertNotIn("missed", text)

    def test_a_quiet_camera_says_nothing_not_zero(self):
        self.assertEqual(clips.summarise({"Side": []}, NOW), "Side: nothing")

    def test_motion_only_is_said_plainly(self):
        text = clips.summarise({"Front": [clip(60, MOTION)]}, NOW)
        self.assertIn("motion only", text)

    def test_older_than_the_window_is_excluded(self):
        text = clips.summarise({"Front": [clip(200_000, PERSON)]}, NOW)
        self.assertEqual(text, "Front: nothing")


class Retention(unittest.TestCase):
    def test_a_start_time_is_read_back_from_the_download_path(self):
        """Rather than a second index that could disagree with the disk."""
        self.assertIn("def _start_from_path", MEDIA)
        self.assertIn('"%Y-%m-%d %H%M%S"', MEDIA)

    def test_an_unparseable_path_is_not_protected_by_accident(self):
        body = MEDIA.split("def _start_from_path", 1)[1].split("def ", 1)[0]
        self.assertIn("except (ValueError, TypeError):", body)
        self.assertIn("return None", body)

    def test_pruning_can_protect_specific_clips(self):
        self.assertIn("protected: set[int] | None = None", MEDIA)
        self.assertIn("if _start_from_path(video) not in protected", MEDIA)

    def test_presses_are_what_gets_protected(self):
        coordinator = (COMPONENT / "coordinator.py").read_text()
        self.assertIn("event_type(clip) == EVENT_RING", coordinator)


class Captioning(unittest.TestCase):
    def test_the_agent_must_be_named(self):
        """Guessing could send a picture of a doorstep to a cloud service."""
        self.assertIn('vol.Required("agent_id")', SERVICES_SRC)

    def test_the_prompt_forbids_speculation(self):
        """One frame of someone reading a house number should not come back as
        a suspicious individual loitering."""
        self.assertIn("Do not speculate about intent", const.DESCRIBE_PROMPT)

    def test_a_missing_thumbnail_is_explained_not_crashed(self):
        body = SERVICES_SRC.split("async def describe_recording", 1)[1][:1200]
        self.assertIn("ServiceValidationError", body)

    def test_no_ai_configured_says_so(self):
        body = SERVICES_SRC.split("async def describe_recording", 1)[1][:2500]
        self.assertIn("No AI service is available", body)


class Digest(unittest.TestCase):
    def test_it_is_a_service_not_a_schedule(self):
        """Off unless something calls it: a summary nobody asked for is what
        makes people mute an integration."""
        self.assertIn("SERVICE_DAILY_SUMMARY", SERVICES_SRC)
        for scheduler in ("async_track_time_change", "async_track_utc_time_change"):
            self.assertNotIn(scheduler, SERVICES_SRC)
            self.assertNotIn(scheduler, INIT)

    def test_it_shares_its_phrasing_with_the_voice_answer(self):
        self.assertIn("summarise(", SERVICES_SRC)
        self.assertIn("summarise(", INTENT)


class Voice(unittest.TestCase):
    def test_both_questions_are_handled(self):
        self.assertIn("TapoH500LastEvent", INTENT)
        self.assertIn("TapoH500Today", INTENT)

    def test_sentences_exist_for_them(self):
        sentences = (COMPONENT / "intents" / "en.yaml").read_text()
        self.assertIn("who was at the door", sentences)
        self.assertIn("TapoH500Today", sentences)

    def test_durations_are_spoken_not_counted(self):
        intent_mod = importlib.import_module("tapo_h500.intent")
        self.assertEqual(intent_mod._ago(30), "just now")
        self.assertEqual(intent_mod._ago(3600 * 5), "5 hours ago")
        self.assertEqual(intent_mod._ago(86400), "1 day ago")

    def test_nothing_recorded_is_an_answer(self):
        self.assertIn("Nothing has been recorded", INTENT)

    def test_voice_never_breaks_setup(self):
        block = INIT.split("from .intent import async_setup_intents", 1)[1][:200]
        self.assertIn("except Exception", block)


if __name__ == "__main__":
    unittest.main()


class Verification(unittest.TestCase):
    """A truncated download looks identical to a good one on disk."""

    def test_a_clip_is_checked_after_downloading(self):
        coordinator = (COMPONENT / "coordinator.py").read_text()
        self.assertIn("async_verify(self.hass, stored)", coordinator)

    def test_it_is_checked_before_anything_is_pruned(self):
        """Discovering a bad clip later means discovering it is gone.

        Scoped to the download function: slicing from the first mention of
        async_download_clip lands on the IMPORT line, where async_prune sits
        two characters later and the order means nothing.
        """
        coordinator = (COMPONENT / "coordinator.py").read_text()
        body = coordinator.split("def _download_new", 1)[1]
        self.assertLess(body.index("async_verify"), body.index("async_prune"))

    def test_a_bad_clip_is_removed_so_it_can_be_fetched_again(self):
        coordinator = (COMPONENT / "coordinator.py").read_text()
        self.assertIn("stored.unlink", coordinator)

    def test_it_decodes_rather_than_reading_a_header(self):
        self.assertIn('"-f", "null", "-"', MEDIA)
        self.assertIn('"-xerror"', MEDIA)


class Export(unittest.TestCase):
    def test_the_destination_must_be_allowed(self):
        """Writing anywhere the process can reach would let a service call
        reach the whole filesystem."""
        body = MEDIA.split("async def async_export", 1)[1]
        self.assertIn("is_allowed_path", body)

    def test_it_copies_rather_than_moves(self):
        """The media directory stays the working set; moving would break every
        card pointing at it."""
        body = MEDIA.split("async def async_export", 1)[1].split("async def", 1)[0]
        self.assertIn("shutil.copy2", body)
        self.assertNotIn("shutil.move", body)

    def test_an_undownloaded_clip_is_refused_clearly(self):
        body = MEDIA.split("async def async_export", 1)[1]
        self.assertIn("has not been downloaded", body)

    def test_the_thumbnail_goes_with_it(self):
        body = MEDIA.split("async def async_export", 1)[1].split("async def", 1)[0]
        self.assertIn('".jpg"', body)

    def test_there_is_no_default_destination(self):
        """Copying files somewhere is not a thing to guess at."""
        self.assertIn('vol.Required("destination")', SERVICES_SRC)
