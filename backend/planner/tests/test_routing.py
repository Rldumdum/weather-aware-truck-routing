from unittest import mock

from django.test import SimpleTestCase

from planner.geo import Path, Point
from planner.routing import (
    MAX_DETOUR,
    RouteCandidate,
    RoutingError,
    _parse_osrm_route,
    _parse_valhalla_trip,
    _road_name,
    _RouteSet,
    _shape_seconds,
    generate_routes,
)

ORIGIN = (40.0, -105.0)
DESTINATION = (40.0, -100.0)


def encode(points: list[Point]) -> str:
    """Google polyline encoder at precision 6, the inverse of geo.decode_polyline."""
    output = []
    previous = (0, 0)
    for point in points:
        scaled = (round(point[0] * 1e6), round(point[1] * 1e6))
        for value, last in zip(scaled, previous):
            delta = value - last
            remaining = ~(delta << 1) if delta < 0 else delta << 1
            while remaining >= 0x20:
                output.append(chr((0x20 | (remaining & 0x1F)) + 63))
                remaining >>= 5
            output.append(chr(remaining + 63))
        previous = scaled
    return "".join(output)


def candidate(lat_offset=0.0, seconds=18000.0, source="valhalla-truck") -> RouteCandidate:
    """A route bowing north by lat_offset degrees at its midpoint."""
    points = [ORIGIN, (40.0 + lat_offset, -102.5), DESTINATION]
    return RouteCandidate(Path(points, [0.0, seconds / 2, seconds]), ["I-70"], source)


class Distinctness(SimpleTestCase):
    def test_keeps_a_genuinely_different_route(self):
        routes = _RouteSet()
        routes.add(candidate())
        routes.add(candidate(lat_offset=1.0))
        self.assertEqual(len(routes.routes), 2)

    def test_rejects_a_near_copy(self):
        routes = _RouteSet()
        routes.add(candidate())
        routes.add(candidate(lat_offset=0.001))
        self.assertEqual(len(routes.routes), 1)

    def test_rejects_an_excessive_detour(self):
        routes = _RouteSet()
        routes.add(candidate(seconds=18000))
        routes.add(candidate(lat_offset=1.0, seconds=18000 * (MAX_DETOUR + 0.1)))
        self.assertEqual(len(routes.routes), 1)


class Generation(SimpleTestCase):
    def test_uses_alternates_without_extra_calls_when_three_come_back(self):
        three = [candidate(seconds=20000), candidate(1.0, 18000), candidate(-1.0, 19000)]
        with mock.patch("planner.routing.fetch_valhalla", return_value=three) as fetch:
            routes = generate_routes(ORIGIN, DESTINATION, 20_000)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([r.path.duration_s for r in routes], [18000, 19000, 20000])

    def test_via_points_fill_in_when_the_router_returns_too_few(self):
        results = [[candidate()], [candidate(1.0)], [candidate(-1.0)]]
        with mock.patch("planner.routing.fetch_valhalla", side_effect=results):
            routes = generate_routes(ORIGIN, DESTINATION, 20_000)
        self.assertEqual(len(routes), 3)

    def test_falls_back_to_osrm_when_valhalla_refuses(self):
        three = [candidate(0, 18000, "osrm-car"), candidate(1.0, 19000, "osrm-car"), candidate(-1.0, 20000, "osrm-car")]
        with mock.patch("planner.routing.fetch_valhalla", side_effect=RoutingError("too far")), mock.patch(
            "planner.routing.fetch_osrm", return_value=three
        ):
            routes = generate_routes(ORIGIN, DESTINATION, 20_000)
        self.assertEqual({r.source for r in routes}, {"osrm-car"})

    def test_a_failed_via_attempt_does_not_lose_found_routes(self):
        with mock.patch("planner.routing.fetch_valhalla", side_effect=[[candidate()]] + [RoutingError("x")] * 8):
            self.assertEqual(len(generate_routes(ORIGIN, DESTINATION, 20_000)), 1)


class RoadNames(SimpleTestCase):
    def test_normalizes_numbered_highways(self):
        cases = {
            "I 80": "I-80",
            "I 8 East": "I-8",
            "I-40 W": "I-40",
            "US 287": "US-287",
            "CA 60 East": "CA-60",
            "I 80; US 30": "I-80",
            "Colfax Avenue": "Colfax Avenue",
        }
        for raw, expected in cases.items():
            self.assertEqual(_road_name(raw), expected)


class Parsing(SimpleTestCase):
    SHAPE = encode([(40.0, -105.0), (40.0, -104.0)])

    def test_maneuver_time_is_spread_by_distance(self):
        points = [(0.0, 0.0), (0.0, 1.0), (0.0, 3.0)]
        seconds = _shape_seconds(points, [{"begin_shape_index": 0, "end_shape_index": 2, "time": 300}])
        self.assertAlmostEqual(seconds[1], 100, delta=0.5)
        self.assertAlmostEqual(seconds[2], 300)

    def test_valhalla_trip_keeps_the_primary_road_name(self):
        trip = {
            "legs": [
                {
                    "shape": self.SHAPE,
                    "maneuvers": [
                        {
                            "begin_shape_index": 0,
                            "end_shape_index": 1,
                            "time": 3000,
                            "length": 50,
                            "street_names": ["I-70", "US-40"],
                        },
                        {"begin_shape_index": 1, "end_shape_index": 1, "time": 0, "length": 0},
                    ],
                }
            ]
        }
        route = _parse_valhalla_trip(trip)
        self.assertEqual(route.roads, ["I-70"])
        self.assertEqual(route.path.duration_s, 3000)

    def test_osrm_route_uses_segment_durations(self):
        route = _parse_osrm_route(
            {
                "geometry": self.SHAPE,
                "legs": [
                    {
                        "annotation": {"duration": [1200.0]},
                        "steps": [{"ref": "I-80; US-30", "distance": 1000}, {"name": "", "distance": 10}],
                    }
                ],
            }
        )
        self.assertEqual(route.path.duration_s, 1200)
        self.assertEqual(route.roads, ["I-80"])
