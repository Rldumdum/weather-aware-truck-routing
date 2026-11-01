"""Open-Meteo hourly forecasts, cached per map cell.

Forecasts are keyed by a snapped cell rather than the exact point so overlapping
routes, repeat searches and the heatmap share data. Open-Meteo counts every
location against the free quota, which makes this the main cost control.
"""

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from .geo import Point
from .risk import WeatherSample

logger = logging.getLogger(__name__)

BASE_CELL_DEG = 0.1  # about 7 miles
MAX_LOCATIONS = 320  # with the 240-point heatmap, one trip stays under 600 calls/minute
CHUNK_SIZE = 100
MAX_WORKERS = 4
CACHE_SECONDS = 30 * 60
HOURLY_VARIABLES = "wind_speed_10m,wind_gusts_10m,rain,showers,snowfall"
FORECAST_HOURS = 240  # covers a departure 7 days out plus a long trip
RETRY_DELAYS = (1.5, 4.0)


class WeatherError(Exception):
    pass


@dataclass
class HourlyForecast:
    """Parallel hourly arrays, timestamps in Unix seconds on the hour."""

    times: list[int]
    wind: list[float]
    gust: list[float]
    rain: list[float]
    snow: list[float]

    def sample(self, timestamp: float) -> WeatherSample:
        """Weather at an arbitrary time.

        Wind is an instantaneous reading, so it is interpolated between hours.
        Open-Meteo reports rain and snow as the total for the preceding hour, so
        those come from the bucket that contains the timestamp.
        """
        start, end = self.times[0], self.times[-1]
        timestamp = min(max(timestamp, start), end)
        position = (timestamp - start) / 3600
        before = int(position)
        after = min(math.ceil(position), len(self.times) - 1)
        f = position - before
        return WeatherSample(
            wind_mph=self.wind[before] + (self.wind[after] - self.wind[before]) * f,
            gust_mph=self.gust[before] + (self.gust[after] - self.gust[before]) * f,
            rain_in_hr=self.rain[after],
            snow_in_hr=self.snow[after],
        )


def cell_of(point: Point, cell_deg: float = BASE_CELL_DEG) -> Point:
    return (
        round(round(point[0] / cell_deg) * cell_deg, 3),
        round(round(point[1] / cell_deg) * cell_deg, 3),
    )


def cell_size_for(points: list[Point]) -> float:
    """Smallest cell keeping a request within MAX_LOCATIONS unique lookups."""
    cell_deg = BASE_CELL_DEG
    while len({cell_of(point, cell_deg) for point in points}) > MAX_LOCATIONS:
        cell_deg = round(cell_deg + 0.05, 2)
    return cell_deg


def _cache_key(cell: Point) -> str:
    return f"wx:{cell[0]:.3f}:{cell[1]:.3f}"


def _numbers(values: list[float | None]) -> list[float]:
    return [value or 0.0 for value in values]


def _parse_location(entry: dict) -> HourlyForecast:
    hourly = entry["hourly"]
    rain = [a + b for a, b in zip(_numbers(hourly["rain"]), _numbers(hourly["showers"]))]
    return HourlyForecast(
        times=hourly["time"],
        wind=_numbers(hourly["wind_speed_10m"]),
        gust=_numbers(hourly["wind_gusts_10m"]),
        rain=rain,
        snow=_numbers(hourly["snowfall"]),
    )


def _fetch_chunk(cells: list[Point]) -> dict[Point, HourlyForecast]:
    params = {
        "latitude": ",".join(f"{lat:.3f}" for lat, _ in cells),
        "longitude": ",".join(f"{lon:.3f}" for _, lon in cells),
        "hourly": HOURLY_VARIABLES,
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timeformat": "unixtime",
        "timezone": "GMT",
        "past_hours": 2,
        "forecast_hours": FORECAST_HOURS,
    }
    if settings.OPEN_METEO_API_KEY:
        params["apikey"] = settings.OPEN_METEO_API_KEY
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            response = requests.post(settings.OPEN_METEO_URL, data=params, timeout=20, headers=settings.HTTP_HEADERS)
            if response.status_code == 200:
                payload = response.json()
                entries = payload if isinstance(payload, list) else [payload]
                return {cell: _parse_location(entry) for cell, entry in zip(cells, entries)}
            failure = f"HTTP {response.status_code}: {response.text[:200]}"
            retryable = response.status_code == 429 or response.status_code >= 500
        except requests.RequestException as exc:
            failure, retryable = str(exc), True
        if delay is None or not retryable:
            raise WeatherError(f"Open-Meteo request failed: {failure}")
        logger.warning("Open-Meteo attempt %s failed: %s", attempt + 1, failure)
        time.sleep(delay)
    raise WeatherError("Open-Meteo request failed")


def forecasts_for(cells: set[Point]) -> dict[Point, HourlyForecast]:
    """Forecasts for each cell, from the cache where possible."""
    keys = {_cache_key(cell): cell for cell in cells}
    cached = cache.get_many(keys)
    result = {keys[key]: forecast for key, forecast in cached.items()}
    missing = [cell for key, cell in keys.items() if key not in cached]
    if missing:
        chunks = [missing[i : i + CHUNK_SIZE] for i in range(0, len(missing), CHUNK_SIZE)]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for fetched in pool.map(_fetch_chunk, chunks):
                result.update(fetched)
                cache.set_many({_cache_key(cell): f for cell, f in fetched.items()}, CACHE_SECONDS)
    return result
