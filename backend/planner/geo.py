"""Geometry helpers. Points are (lat, lon) tuples throughout the backend."""

import math
from bisect import bisect_right
from dataclasses import dataclass

EARTH_RADIUS_MI = 3958.8
EARTH_CIRCUMFERENCE_MI = 2 * math.pi * EARTH_RADIUS_MI

Point = tuple[float, float]


def haversine_mi(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(h))


def decode_polyline(encoded: str, precision: int = 6) -> list[Point]:
    """Decode a Google-format polyline (Valhalla and OSRM both emit precision 6)."""
    factor = 10.0**precision
    points: list[Point] = []
    index = lat = lon = 0
    while index < len(encoded):
        for is_lon in (False, True):
            result = shift = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lon:
                lon += delta
            else:
                lat += delta
        points.append((lat / factor, lon / factor))
    return points


@dataclass(frozen=True)
class Position:
    lat: float
    lon: float
    seconds: float  # drive time from the origin


class Path:
    """A driven polyline with cumulative distance and drive time per vertex.

    Using the router's own per-segment times (not distance / average speed) is
    what makes checkpoint ETAs honest: 25 miles of mountain pass takes longer
    than 25 miles of interstate.
    """

    def __init__(self, points: list[Point], seconds: list[float]):
        if len(points) < 2 or len(points) != len(seconds):
            raise ValueError("a path needs matching points and seconds, at least 2 of each")
        self.points = points
        self.seconds = seconds
        self.miles = [0.0]
        for a, b in zip(points, points[1:]):
            self.miles.append(self.miles[-1] + haversine_mi(a, b))

    @property
    def length_mi(self) -> float:
        return self.miles[-1]

    @property
    def duration_s(self) -> float:
        return self.seconds[-1]

    def at_mile(self, mile: float) -> Position:
        mile = min(max(mile, 0.0), self.length_mi)
        i = min(bisect_right(self.miles, mile), len(self.miles) - 1)
        span = self.miles[i] - self.miles[i - 1]
        f = (mile - self.miles[i - 1]) / span if span else 0.0
        a, b = self.points[i - 1], self.points[i]
        return Position(
            lat=a[0] + (b[0] - a[0]) * f,
            lon=a[1] + (b[1] - a[1]) * f,
            seconds=self.seconds[i - 1] + (self.seconds[i] - self.seconds[i - 1]) * f,
        )

    def resampled(self, step_mi: float) -> list[Point]:
        count = max(int(self.length_mi // step_mi), 1)
        marks = [self.length_mi * i / count for i in range(count + 1)]
        return [(p.lat, p.lon) for p in map(self.at_mile, marks)]


def simplify_indices(points: list[Point], tolerance_deg: float) -> list[int]:
    """Ramer-Douglas-Peucker, iterative. Returns the vertex indices to keep."""
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        ax, ay = points[first]
        bx, by = points[last]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, worst_index = 0.0, -1
        for i in range(first + 1, last):
            px, py = points[i]
            if norm:
                distance = abs(dy * (px - ax) - dx * (py - ay)) / norm
            else:
                distance = math.hypot(px - ax, py - ay)
            if distance > worst:
                worst, worst_index = distance, i
        if worst > tolerance_deg:
            keep.add(worst_index)
            stack.append((first, worst_index))
            stack.append((worst_index, last))
    return sorted(keep)


def mercator(lat: float, lon: float) -> tuple[float, float]:
    """Project to MapLibre's normalized mercator space, 0..1 on both axes."""
    x = (lon + 180.0) / 360.0
    sin_lat = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)
    return x, y


def inverse_mercator(x: float, y: float) -> Point:
    lon = x * 360.0 - 180.0
    lat = math.degrees(2 * math.atan(math.exp((0.5 - y) * 2 * math.pi)) - math.pi / 2)
    return lat, lon


def miles_per_mercator_unit(lat: float) -> float:
    return EARTH_CIRCUMFERENCE_MI * math.cos(math.radians(lat))
