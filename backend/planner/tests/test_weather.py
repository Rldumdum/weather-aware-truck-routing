from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from planner.weather import (
    BASE_CELL_DEG,
    MAX_LOCATIONS,
    HourlyForecast,
    WeatherError,
    cell_of,
    cell_size_for,
    forecasts_for,
)


def forecast() -> HourlyForecast:
    return HourlyForecast(
        times=[0, 3600, 7200],
        wind=[10.0, 20.0, 40.0],
        gust=[15.0, 25.0, 45.0],
        rain=[0.0, 0.2, 0.6],
        snow=[0.0, 0.0, 1.5],
    )


class Sampling(SimpleTestCase):
    def test_wind_interpolates_between_hours(self):
        self.assertAlmostEqual(forecast().sample(1800).wind_mph, 15)
        self.assertAlmostEqual(forecast().sample(5400).wind_mph, 30)

    def test_precip_uses_the_hour_ending_after_the_timestamp(self):
        sample = forecast().sample(1800)  # inside the 00:00-01:00 bucket, stamped 01:00
        self.assertEqual(sample.rain_in_hr, 0.2)
        self.assertEqual(sample.snow_in_hr, 0.0)

    def test_on_the_hour_reads_that_hour(self):
        self.assertEqual(forecast().sample(3600).rain_in_hr, 0.2)

    def test_clamps_outside_the_forecast(self):
        self.assertEqual(forecast().sample(-100).wind_mph, 10)
        self.assertEqual(forecast().sample(99_999).wind_mph, 40)


class Cells(SimpleTestCase):
    def test_nearby_points_share_a_cell(self):
        self.assertEqual(cell_of((39.742, -104.991)), cell_of((39.749, -104.988)))

    def test_cell_grows_only_when_a_trip_needs_too_many_lookups(self):
        few = [(40.0, -100.0 + i * 0.1) for i in range(10)]
        many = [(40.0 + j * 0.1, -100.0 + i * 0.1) for i in range(40) for j in range(40)]
        self.assertEqual(cell_size_for(few), BASE_CELL_DEG)
        coarse = cell_size_for(many)
        self.assertGreater(coarse, BASE_CELL_DEG)
        self.assertLessEqual(len({cell_of(p, coarse) for p in many}), MAX_LOCATIONS)


class Fetching(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.cell = (40.0, -100.0)

    def _response(self, status=200, payload=None):
        response = mock.Mock(status_code=status, text="failure")
        response.json.return_value = payload
        return response

    def _payload(self):
        return {
            "hourly": {
                "time": [0, 3600],
                "wind_speed_10m": [5.0, 6.0],
                "wind_gusts_10m": [8.0, 9.0],
                "rain": [0.1, None],
                "showers": [0.05, 0.2],
                "snowfall": [0.0, 0.0],
            }
        }

    def test_rain_combines_rain_and_showers(self):
        with mock.patch("planner.weather.requests.post", return_value=self._response(payload=self._payload())):
            result = forecasts_for({self.cell})
        self.assertAlmostEqual(result[self.cell].rain[0], 0.15)
        self.assertAlmostEqual(result[self.cell].rain[1], 0.2)

    def test_second_lookup_comes_from_the_cache(self):
        with mock.patch("planner.weather.requests.post", return_value=self._response(payload=self._payload())) as post:
            forecasts_for({self.cell})
            forecasts_for({self.cell})
        self.assertEqual(post.call_count, 1)

    def test_rate_limit_is_retried(self):
        responses = [self._response(status=429), self._response(payload=self._payload())]
        with mock.patch("planner.weather.requests.post", side_effect=responses), mock.patch("planner.weather.time.sleep"):
            self.assertIn(self.cell, forecasts_for({self.cell}))

    def test_persistent_failure_raises_instead_of_inventing_weather(self):
        with mock.patch("planner.weather.requests.post", return_value=self._response(status=500)), mock.patch(
            "planner.weather.time.sleep"
        ):
            with self.assertRaises(WeatherError):
                forecasts_for({self.cell})
