"""When the hub will start overwriting.

There is no history to read -- the hub reports how full it is now and nothing
about how full it was -- so the trend has to be sampled while Home Assistant
runs. That makes the interesting cases the ones where the honest answer is "I
do not know": too little history, a disk that is not filling, and a disk that
was emptied halfway through the run.
"""
import asyncio
import importlib
import sys
import unittest
from pathlib import Path

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tapo_h500"
SENSOR = (COMPONENT / "sensor.py").read_text()

sys.path.insert(0, str(Path(__file__).parent))
import test_coordinator as harness  # noqa: E402  (installs the HA stubs)

status = importlib.import_module("tapo_h500.status")
const = importlib.import_module("tapo_h500.const")
coordinator_mod = importlib.import_module("tapo_h500.coordinator")

NOW = 1_786_600_000
HOUR = 3600


def rising(hours, per_hour, start=50.0, step=60):
    """Readings every `step` seconds, climbing at a steady rate."""
    return [(NOW - hours * HOUR + n * step,
             start + per_hour * (n * step) / HOUR)
            for n in range(int(hours * HOUR / step) + 1)]


class FillRate(unittest.TestCase):
    def test_a_steady_climb_is_measured(self):
        rate = status.fill_rate(rising(6, 0.5))
        self.assertAlmostEqual(rate, 0.5, places=6)

    def test_one_reading_says_nothing(self):
        self.assertIsNone(status.fill_rate([(NOW, 50.0)]))

    def test_nothing_at_all_says_nothing(self):
        self.assertIsNone(status.fill_rate([]))

    def test_a_short_run_says_nothing(self):
        """The hub rounds to a tenth of a percent, which on a 512 GB card is
        half a gigabyte. Two readings a minute apart measure the rounding."""
        self.assertIsNone(status.fill_rate(rising(0.5, 0.5)))

    def test_readings_all_at_one_instant_say_nothing(self):
        """Caught by the span check rather than a guard of their own: no
        spread in the timestamps means no span either."""
        self.assertIsNone(status.fill_rate([(NOW, 50.0)] * 100))

    def test_a_falling_disk_gives_a_negative_rate(self):
        self.assertLess(status.fill_rate(rising(6, -0.5)), 0)

    def test_a_single_odd_reading_does_not_dominate(self):
        """Least squares over the whole run, not first-versus-last: the
        rounding makes the endpoints the two least reliable points there are,
        and a line built from them alone swings on either one."""
        samples = rising(6, 0.5)
        samples[-1] = (samples[-1][0], samples[-1][1] + 5)
        self.assertLess(status.fill_rate(samples), 1.0)


class HoursUntilFull(unittest.TestCase):
    def test_it_divides_what_is_left_by_the_rate(self):
        # 50% left at 0.5% an hour is 100 hours.
        self.assertAlmostEqual(
            status.hours_until_full(rising(6, 0.5, start=50.0), 50.0),
            100.0, places=3)

    def test_a_disk_that_is_not_filling_has_no_answer(self):
        """A hub already overwriting sits at a steady figure forever. A line
        fitted to the noise in that would forecast thousands of days."""
        self.assertIsNone(
            status.hours_until_full(rising(6, 0.0, start=99.0), 99.0))

    def test_a_disk_that_is_emptying_has_no_answer(self):
        self.assertIsNone(
            status.hours_until_full(rising(6, -0.5, start=50.0), 50.0))

    def test_an_already_full_disk_is_zero(self):
        """Not a special case in the code: the figure is computed as
        (total - free) / total, so 100 is its ceiling and the arithmetic
        already lands on zero there."""
        self.assertEqual(
            status.hours_until_full(rising(6, 0.5, start=99.0), 100.0), 0.0)

    def test_no_reading_means_no_answer(self):
        self.assertIsNone(status.hours_until_full(rising(6, 0.5), None))

    def test_too_little_history_means_no_answer(self):
        self.assertIsNone(status.hours_until_full(rising(0.2, 0.5), 50.0))


class TrendSamples(unittest.TestCase):
    def test_a_reading_is_appended(self):
        self.assertEqual(status.trend_samples([], NOW, 50.0, 10),
                         [(NOW, 50.0)])

    def test_a_missing_reading_changes_nothing(self):
        history = [(NOW, 50.0)]
        self.assertEqual(status.trend_samples(history, NOW + 60, None, 10),
                         history)

    def test_the_history_is_capped(self):
        history = [(NOW + n, 50.0) for n in range(10)]
        kept = status.trend_samples(history, NOW + 10, 51.0, 5)
        self.assertEqual(len(kept), 5)
        self.assertEqual(kept[-1], (NOW + 10, 51.0))

    def test_a_format_starts_the_history_again(self):
        """A format, a swapped card or loop recording finally catching up all
        look the same: the figure falls. Fitting a line across that drop
        forecasts from a slope that never happened."""
        history = [(NOW + n * 60, 90.0) for n in range(10)]
        kept = status.trend_samples(history, NOW + 600, 2.0, 100)
        self.assertEqual(kept, [(NOW + 600, 2.0)])

    def test_ordinary_jitter_does_not(self):
        """The figure is rounded to a tenth of a percent, so it wobbles. That
        is not an emptied disk, and discarding the history on every wobble
        would mean the forecast never had enough of it."""
        history = [(NOW + n * 60, 90.0) for n in range(10)]
        kept = status.trend_samples(history, NOW + 600, 89.9, 100)
        self.assertEqual(len(kept), 11)


class Wiring(unittest.TestCase):
    class _Client:
        used = 50.0

        def cameras(self):
            return [{"device_id": "cam0", "alias": "Front"}]

        def recent(self, camera, start, end):
            return []

        def detections(self, camera, start, end):
            return []

        def hub_status(self):
            return {"used": self.used}

    def _build(self):
        client = self._Client()
        coord = coordinator_mod.H500Coordinator(
            harness._Hass(), harness._Entry(20), client)
        coord._download_new = lambda *a, **k: None
        # hub_readings parses a real response; the shape is not what is under
        # test here, only that a sample is recorded per status refresh.
        coordinator_mod.hub_readings = lambda raw: {
            "storage_used_percent": raw["used"]}
        return coord, client

    def test_a_sample_is_recorded_when_status_refreshes(self):
        coord, _ = self._build()
        try:
            coord.data = asyncio.run(coord._async_update_data())
            self.assertEqual(len(coord.storage_trend), 1)
        finally:
            coordinator_mod.hub_readings = status.hub_readings

    def test_the_forecast_is_unavailable_until_there_is_history(self):
        coord, _ = self._build()
        try:
            coord.data = asyncio.run(coord._async_update_data())
            self.assertIsNone(coord.days_until_full())
        finally:
            coordinator_mod.hub_readings = status.hub_readings

    def test_it_reports_days_rather_than_hours(self):
        coord, _ = self._build()
        coord.readings = {"storage_used_percent": 50.0}
        coord.storage_trend = rising(6, 0.5, start=50.0)
        # 100 hours left.
        self.assertAlmostEqual(coord.days_until_full(), 100 / 24, places=2)


class Entity(unittest.TestCase):
    def test_it_is_added_to_the_hub(self):
        setup = SENSOR.split("async_setup_entry", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("H500StorageForecast(coordinator, entry)", setup)

    def test_it_reports_the_rate_and_the_sample_count(self):
        """So "no forecast" can be read as "not filling" or "still
        measuring" rather than as a fault."""
        body = SENSOR.split("class H500StorageForecast", 1)[1].split(
            "\n\nclass ", 1)[0]
        self.assertIn('"percent_per_hour"', body)
        self.assertIn('"samples"', body)


if __name__ == "__main__":
    unittest.main()
