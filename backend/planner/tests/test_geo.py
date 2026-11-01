from django.test import SimpleTestCase

from planner.geo import Path, decode_polyline, haversine_mi, inverse_mercator, mercator, simplify_indices


class Distance(SimpleTestCase):
    def test_haversine_known_distance(self):
        denver, salt_lake = (39.7392, -104.9903), (40.7608, -111.8910)
        self.assertAlmostEqual(haversine_mi(denver, salt_lake), 371, delta=3)


class Polyline(SimpleTestCase):
    def test_decodes_google_reference_polyline(self):
        points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        expected = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
        for point, reference in zip(points, expected):
            self.assertAlmostEqual(point[0], reference[0], places=5)
            self.assertAlmostEqual(point[1], reference[1], places=5)


class PathInterpolation(SimpleTestCase):
    def setUp(self):
        # Two equal-length legs; the second takes three times as long.
        self.path = Path([(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)], [0.0, 100.0, 400.0])

    def test_time_follows_the_router_not_average_speed(self):
        quarter = self.path.at_mile(self.path.length_mi / 4)
        three_quarters = self.path.at_mile(self.path.length_mi * 3 / 4)
        self.assertAlmostEqual(quarter.seconds, 50)
        self.assertAlmostEqual(three_quarters.seconds, 250)

    def test_clamps_to_the_ends(self):
        self.assertEqual(self.path.at_mile(-5).seconds, 0)
        self.assertEqual(self.path.at_mile(10_000).seconds, 400)


class Simplify(SimpleTestCase):
    def test_drops_collinear_points_and_keeps_corners(self):
        points = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0), (1.0, 2.0)]
        self.assertEqual(simplify_indices(points, 0.01), [0, 2, 3])


class Mercator(SimpleTestCase):
    def test_round_trip(self):
        x, y = mercator(39.7, -105.0)
        lat, lon = inverse_mercator(x, y)
        self.assertAlmostEqual(lat, 39.7)
        self.assertAlmostEqual(lon, -105.0)

    def test_origin_maps_to_center(self):
        self.assertEqual(mercator(0, 0), (0.5, 0.5))
