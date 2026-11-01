from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from planner.routing import RoutingError
from planner.weather import WeatherError

PLACE_A = {"label": "Denver", "lat": 39.74, "lon": -104.99}
PLACE_B = {"label": "Salt Lake City", "lat": 40.76, "lon": -111.89}


def body(**overrides) -> dict:
    return {
        "origin": PLACE_A,
        "destination": PLACE_B,
        "departure": (timezone.now() + timedelta(hours=1)).isoformat(),
        "load_lb": 35_000,
        "sample_interval_mi": 25,
        **overrides,
    }


class PlanValidation(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def post(self, **overrides):
        return self.client.post("/api/plan", body(**overrides), format="json")

    def test_rejects_an_unsupported_sample_interval(self):
        self.assertEqual(self.post(sample_interval_mi=15).status_code, 400)

    def test_rejects_zero_load(self):
        self.assertEqual(self.post(load_lb=0).status_code, 400)

    def test_rejects_past_departure(self):
        departure = (timezone.now() - timedelta(days=1)).isoformat()
        self.assertEqual(self.post(departure=departure).status_code, 400)

    def test_rejects_departure_beyond_the_forecast(self):
        departure = (timezone.now() + timedelta(days=8)).isoformat()
        self.assertEqual(self.post(departure=departure).status_code, 400)

    def test_rejects_identical_places(self):
        self.assertEqual(self.post(destination=PLACE_A).status_code, 400)

    def test_rejects_the_same_place_with_slightly_different_coordinates(self):
        almost_the_same = {"label": "Denver, CO", "lat": 39.7401, "lon": -104.9903}
        self.assertEqual(self.post(destination=almost_the_same).status_code, 400)

    def test_upstream_routing_failure_is_a_clean_503(self):
        with mock.patch("planner.views.generate_routes", side_effect=RoutingError("down")):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn("route", response.json()["detail"])

    def test_upstream_weather_failure_is_a_clean_503(self):
        with mock.patch("planner.views._routes_for", return_value=[mock.Mock()]), mock.patch(
            "planner.views.plan_trip", side_effect=WeatherError("down")
        ):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn("weather", response.json()["detail"])


class Geocode(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_short_query_returns_nothing_without_calling_upstream(self):
        with mock.patch("planner.views.requests.get") as get:
            response = APIClient().get("/api/geocode", {"q": "a"})
        self.assertEqual(response.json(), {"results": []})
        get.assert_not_called()

    def test_formats_labels_and_lat_lon(self):
        feature = {
            "properties": {"name": "Denver", "state": "Colorado", "country": "United States"},
            "geometry": {"coordinates": [-104.98, 39.73]},
        }
        upstream = mock.Mock()
        upstream.json.return_value = {"features": [feature]}
        with mock.patch("planner.views.requests.get", return_value=upstream):
            response = APIClient().get("/api/geocode", {"q": "denver"})
        self.assertEqual(
            response.json()["results"],
            [{"label": "Denver, Colorado, United States", "lat": 39.73, "lon": -104.98}],
        )


    def test_a_city_outranks_a_landmark_named_after_it(self):
        def feature(name, key, value):
            return {
                "properties": {"name": name, "state": "Utah", "country": "United States", "osm_key": key, "osm_value": value},
                "geometry": {"coordinates": [-111.9, 40.7]},
            }

        upstream = mock.Mock()
        upstream.json.return_value = {
            "features": [feature("Salt Lake City Cemetery", "landuse", "cemetery"), feature("Salt Lake City", "place", "city")]
        }
        with mock.patch("planner.views.requests.get", return_value=upstream):
            results = APIClient().get("/api/geocode", {"q": "salt lake city"}).json()["results"]
        self.assertEqual(results[0]["label"], "Salt Lake City, Utah, United States")

    def test_repeated_labels_are_dropped(self):
        same = {
            "properties": {"name": "Denver", "state": "Colorado", "country": "United States"},
            "geometry": {"coordinates": [-104.98, 39.73]},
        }
        upstream = mock.Mock()
        upstream.json.return_value = {"features": [same, same]}
        with mock.patch("planner.views.requests.get", return_value=upstream):
            self.assertEqual(len(APIClient().get("/api/geocode", {"q": "denver"}).json()["results"]), 1)


class Health(SimpleTestCase):
    def test_health(self):
        self.assertEqual(APIClient().get("/api/health").json(), {"status": "ok"})
