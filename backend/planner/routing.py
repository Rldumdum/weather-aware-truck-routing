"""Generate three distinct route options from public routing APIs.

Valhalla is the primary source because it has real truck costing, including
gross weight. Its public server refuses trips over 1,500 km, and neither router
guarantees three alternatives (LA to Phoenix returns one), so:

  1. Ask Valhalla for alternates; on failure fall back to OSRM.
  2. If fewer than three distinct routes came back, force more by routing through
     via points offset sideways from the trip midpoint.
"""

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

import requests
from django.conf import settings

from .geo import Path, Point, haversine_mi, decode_polyline

logger = logging.getLogger(__name__)

TARGET_ROUTES = 3
TRUCK_TARE_LB = 35_000  # tractor plus empty trailer
LB_PER_TONNE = 2204.62
TRUCK_TOP_SPEED_KPH = 105  # about 65 mph, a typical governed fleet truck
MAX_OVERLAP = 0.9  # a route sharing more than this with another is not an option
MAX_DETOUR = 1.6  # never offer a route this many times slower than the fastest
VIA_OFFSETS = (0.12, -0.12, 0.22, -0.22, 0.06, -0.06, 0.35, -0.35)
MAX_VIA_ATTEMPTS = 6


class RoutingError(Exception):
    pass


@dataclass
class RouteCandidate:
    path: Path
    roads: list[str]  # most-travelled roads, longest first
    source: str


RouteFetcher = Callable[[Point, Point, Point | None], list[RouteCandidate]]


def _road_name(raw: str) -> str:
    """Normalize routers' road names: 'I 8 East' and 'I-8 W' both become 'I-8'."""
    name = raw.split(";")[0].strip()
    numbered = re.match(r"^([A-Z]{1,3})[ -](\d+[A-Z]?)\b", name)
    return f"{numbered[1]}-{numbered[2]}" if numbered else name


def _top_roads(miles_by_road: Counter) -> list[str]:
    return [road for road, _ in miles_by_road.most_common(2)]


def _shape_seconds(points: list[Point], maneuvers: list[dict]) -> list[float]:
    """Spread each maneuver's time over its shape vertices, proportional to distance."""
    seconds = [0.0] * len(points)
    elapsed = 0.0
    for maneuver in maneuvers:
        start, end = maneuver["begin_shape_index"], maneuver["end_shape_index"]
        spans = [haversine_mi(points[i], points[i + 1]) for i in range(start, end)]
        total = sum(spans)
        for offset, span in enumerate(spans):
            share = span / total if total else 1 / len(spans)
            elapsed += maneuver["time"] * share
            seconds[start + offset + 1] = elapsed
    return seconds


def _parse_valhalla_trip(trip: dict) -> RouteCandidate:
    points: list[Point] = []
    seconds: list[float] = []
    roads: Counter = Counter()
    for leg in trip["legs"]:
        leg_points = decode_polyline(leg["shape"])
        leg_seconds = _shape_seconds(leg_points, leg["maneuvers"])
        offset = seconds[-1] if seconds else 0.0
        skip = 1 if points else 0  # legs share their joining vertex
        points.extend(leg_points[skip:])
        seconds.extend(s + offset for s in leg_seconds[skip:])
        for maneuver in leg["maneuvers"]:
            for name in maneuver.get("street_names", [])[:1]:
                roads[_road_name(name)] += maneuver.get("length", 0)
    return RouteCandidate(Path(points, seconds), _top_roads(roads), "valhalla-truck")


def fetch_valhalla(origin: Point, destination: Point, via: Point | None, load_lb: float) -> list[RouteCandidate]:
    locations = [{"lat": origin[0], "lon": origin[1]}]
    if via:
        locations.append({"lat": via[0], "lon": via[1], "type": "through", "radius": 8000})
    locations.append({"lat": destination[0], "lon": destination[1]})
    body = {
        "locations": locations,
        "costing": "truck",
        "costing_options": {
            "truck": {
                "weight": round((TRUCK_TARE_LB + load_lb) / LB_PER_TONNE, 1),
                "top_speed": TRUCK_TOP_SPEED_KPH,
            }
        },
        "directions_options": {"units": "miles"},
    }
    if via is None:
        body["alternates"] = TARGET_ROUTES - 1
    try:
        response = requests.post(settings.VALHALLA_URL, json=body, timeout=25, headers=settings.HTTP_HEADERS)
    except requests.RequestException as exc:
        raise RoutingError(f"Valhalla unreachable: {exc}") from exc
    if response.status_code != 200:
        raise RoutingError(f"Valhalla returned {response.status_code}: {response.text[:200]}")
    data = response.json()
    trips = [data["trip"]] + [alternate["trip"] for alternate in data.get("alternates", [])]
    return [_parse_valhalla_trip(trip) for trip in trips]


def _parse_osrm_route(route: dict) -> RouteCandidate:
    points = decode_polyline(route["geometry"])
    seconds = [0.0]
    roads: Counter = Counter()
    for leg in route["legs"]:
        for duration in leg["annotation"]["duration"]:
            seconds.append(seconds[-1] + duration)
        for step in leg["steps"]:
            name = step.get("ref") or step.get("name")
            if name:
                roads[_road_name(name)] += step["distance"]
    return RouteCandidate(Path(points, seconds), _top_roads(roads), "osrm-car")


def fetch_osrm(origin: Point, destination: Point, via: Point | None) -> list[RouteCandidate]:
    waypoints = [origin, *([via] if via else []), destination]
    coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints)
    params = {
        "overview": "full",
        "geometries": "polyline6",
        "annotations": "duration",
        "steps": "true",
        "alternatives": "false" if via else "3",
    }
    try:
        response = requests.get(
            f"{settings.OSRM_URL}/{coordinates}", params=params, timeout=25, headers=settings.HTTP_HEADERS
        )
    except requests.RequestException as exc:
        raise RoutingError(f"OSRM unreachable: {exc}") from exc
    if response.status_code != 200:
        raise RoutingError(f"OSRM returned {response.status_code}: {response.text[:200]}")
    return [_parse_osrm_route(route) for route in response.json()["routes"]]


def _footprint(path: Path) -> set[tuple[int, int]]:
    """Grid cells (about 2 miles) a path passes through, for cheap overlap checks."""
    return {(round(lat / 0.03), round(lon / 0.03)) for lat, lon in path.resampled(1.0)}


def _overlap(a: set[tuple[int, int]], b: set[tuple[int, int]]) -> float:
    near = sum(
        any((x + dx, y + dy) in b for dx in (-1, 0, 1) for dy in (-1, 0, 1))
        for x, y in a
    )
    return near / len(a)


def _via_point(origin: Point, destination: Point, offset: float) -> Point:
    """Point beside the trip midpoint; offset is a fraction of the trip length."""
    mid_lat = (origin[0] + destination[0]) / 2
    x_scale = math.cos(math.radians(mid_lat))  # degrees of longitude are shorter
    dx = (destination[1] - origin[1]) * x_scale
    dy = destination[0] - origin[0]
    lat = mid_lat + dx * offset
    lon = (origin[1] + destination[1]) / 2 - dy * offset / x_scale
    return lat, lon


class _RouteSet:
    """Collects candidates, keeping only distinct and reasonably direct ones."""

    def __init__(self) -> None:
        self.routes: list[RouteCandidate] = []
        self._footprints: list[set[tuple[int, int]]] = []

    def add(self, candidate: RouteCandidate) -> None:
        if self.routes:
            fastest = min(route.path.duration_s for route in self.routes)
            if candidate.path.duration_s > fastest * MAX_DETOUR:
                return
        footprint = _footprint(candidate.path)
        for existing in self._footprints:
            if _overlap(footprint, existing) > MAX_OVERLAP:
                return
        self.routes.append(candidate)
        self._footprints.append(footprint)


def generate_routes(origin: Point, destination: Point, load_lb: float) -> list[RouteCandidate]:
    """Return up to three distinct routes, fastest first."""

    def valhalla(a: Point, b: Point, via: Point | None) -> list[RouteCandidate]:
        return fetch_valhalla(a, b, via, load_lb)

    fetch: RouteFetcher = valhalla
    try:
        candidates = fetch(origin, destination, None)
    except RoutingError as exc:
        logger.warning("falling back to OSRM: %s", exc)
        fetch = fetch_osrm
        candidates = fetch(origin, destination, None)

    collected = _RouteSet()
    for candidate in sorted(candidates, key=lambda c: c.path.duration_s):
        collected.add(candidate)

    for offset in VIA_OFFSETS[:MAX_VIA_ATTEMPTS]:
        if len(collected.routes) >= TARGET_ROUTES:
            break
        try:
            for candidate in fetch(origin, destination, _via_point(origin, destination, offset)):
                collected.add(candidate)
        except RoutingError as exc:
            logger.info("via attempt %s failed: %s", offset, exc)

    return sorted(collected.routes[:TARGET_ROUTES], key=lambda c: c.path.duration_s)
