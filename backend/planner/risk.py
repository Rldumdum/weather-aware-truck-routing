"""Weather risk rules from the brief. Pure functions: no I/O, no Django.

Thresholds are half-open so every real value lands in exactly one class:

    Wind  [0,25)  [25,35)  [35,45)  [45,55)   [55,inf)     No Travel at >= 55
    Rain  [0,.10) [.10,.25) [.25,.50) [.50,1.0] (1.0,inf)  No Travel strictly > 1.00
    Snow  [0,.5)  [.5,1)   [1,2)    [2,3]     (3,inf)      No Travel strictly > 3.0

The brief's rain and snow bands share their edges (0.10-0.25, 0.25-0.50), so a
value exactly on a shared edge takes the higher class: the safer reading.
"""

from dataclasses import dataclass
from enum import IntEnum


class Risk(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


WIND_EDGES = (25.0, 35.0, 45.0, 55.0)
RAIN_EDGES = (0.10, 0.25, 0.50, 1.00)
SNOW_EDGES = (0.5, 1.0, 2.0, 3.0)

NO_TRAVEL_WIND_LOAD_LB = 30_000
SEVERE_WIND_LOAD_LB = 40_000


@dataclass(frozen=True)
class WeatherSample:
    wind_mph: float
    gust_mph: float
    rain_in_hr: float
    snow_in_hr: float


@dataclass(frozen=True)
class Assessment:
    level: Risk
    wind: Risk
    rain: Risk
    snow: Risk
    load_adjusted: bool  # True when a load rule raised the wind class

    @property
    def drivers(self) -> list[str]:
        """Conditions responsible for the overall level, empty when Low."""
        if self.level == Risk.LOW:
            return []
        by_condition = {"wind": self.wind, "rain": self.rain, "snow": self.snow}
        return [name for name, level in by_condition.items() if level == self.level]


def _classify(value: float, edges: tuple[float, ...], *, top_inclusive: bool) -> Risk:
    for level, edge in enumerate(edges[:-1]):
        if value < edge:
            return Risk(level)
    top = edges[-1]
    exceeded = value >= top if top_inclusive else value > top
    return Risk.NO_TRAVEL if exceeded else Risk.SEVERE


def base_wind_risk(mph: float) -> Risk:
    return _classify(mph, WIND_EDGES, top_inclusive=True)


def wind_risk(mph: float, load_lb: float) -> Risk:
    """Wind class after applying the load rules."""
    base = base_wind_risk(mph)
    if base == Risk.SEVERE and load_lb > NO_TRAVEL_WIND_LOAD_LB:
        return Risk.NO_TRAVEL
    if base == Risk.HIGH and load_lb > SEVERE_WIND_LOAD_LB:
        return Risk.SEVERE
    return base


def rain_risk(in_hr: float) -> Risk:
    return _classify(in_hr, RAIN_EDGES, top_inclusive=False)


def snow_risk(in_hr: float) -> Risk:
    return _classify(in_hr, SNOW_EDGES, top_inclusive=False)


def assess(weather: WeatherSample, load_lb: float) -> Assessment:
    wind = wind_risk(weather.wind_mph, load_lb)
    rain = rain_risk(weather.rain_in_hr)
    snow = snow_risk(weather.snow_in_hr)
    return Assessment(
        level=max(wind, rain, snow),
        wind=wind,
        rain=rain,
        snow=snow,
        load_adjusted=wind != base_wind_risk(weather.wind_mph),
    )


def _score(value: float, edges: tuple[float, ...], level: Risk) -> float:
    """Continuous score for the heatmap: floor(score) is always the class.

    The fractional part is how far the value sits through its original band, so
    the map shows gradients inside a class without ever contradicting it.
    """
    if level == Risk.NO_TRAVEL:
        return float(Risk.NO_TRAVEL)
    band = min(sum(value >= edge for edge in edges), len(edges) - 1)
    low = edges[band - 1] if band else 0.0
    return level + min((value - low) / (edges[band] - low), 0.999)


def wind_score(mph: float, load_lb: float) -> float:
    return _score(mph, WIND_EDGES, wind_risk(mph, load_lb))


def rain_score(in_hr: float) -> float:
    return _score(in_hr, RAIN_EDGES, rain_risk(in_hr))


def snow_score(in_hr: float) -> float:
    return _score(in_hr, SNOW_EDGES, snow_risk(in_hr))
