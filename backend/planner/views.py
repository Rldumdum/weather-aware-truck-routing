from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .analysis import plan_trip
from .heatmap import build_heatmap
from .routing import RoutingError, generate_routes
from .serializers import HeatmapRequestSerializer, PlanRequestSerializer
from .weather import WeatherError

GEOCODE_CACHE_SECONDS = 24 * 3600
ROUTE_CACHE_SECONDS = 15 * 60
GEOCODE_LIMIT = 6
GEOCODE_CANDIDATES = 15  # fetched before ranking, so a city can outrank a landmark named after it
PLACE_PRIORITY = {"city": 0, "town": 1, "village": 2, "suburb": 3, "county": 4, "state": 5}
US_CENTER = {"lat": 39.5, "lon": -98.35}  # biases search results toward US freight


def _places(features: list[dict]) -> list[dict]:
    """Settlements first, then Photon's own order, with repeated labels dropped."""

    def priority(feature: dict) -> int:
        properties = feature["properties"]
        return PLACE_PRIORITY.get(properties.get("osm_value"), len(PLACE_PRIORITY)) if properties.get("osm_key") == "place" else len(PLACE_PRIORITY) + 1

    places: dict[str, dict] = {}
    for feature in sorted(features, key=priority):
        label = _place_label(feature["properties"])
        lon, lat = feature["geometry"]["coordinates"]
        places.setdefault(label, {"label": label, "lat": lat, "lon": lon})
    return list(places.values())[:GEOCODE_LIMIT]


class PlanThrottle(AnonRateThrottle):
    rate = "30/min"


def _upstream_failure(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _place_label(properties: dict) -> str:
    parts = [properties.get("name") or properties.get("street"), properties.get("state"), properties.get("country")]
    return ", ".join(dict.fromkeys(part for part in parts if part))


def _routes_for(origin: tuple, destination: tuple, load_lb: float) -> list:
    """Routes don't depend on the sampling interval, so changing it skips the router."""
    key = f"routes:{origin[0]:.4f},{origin[1]:.4f}:{destination[0]:.4f},{destination[1]:.4f}:{load_lb:.0f}"
    routes = cache.get(key)
    if routes is None:
        routes = generate_routes(origin, destination, load_lb)
        cache.set(key, routes, ROUTE_CACHE_SECONDS)
    return routes


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok"})


class GeocodeView(APIView):
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if len(query) < 2:
            return Response({"results": []})
        cache_key = f"geocode:{quote(query.lower()[:100])}"
        results = cache.get(cache_key)
        if results is None:
            try:
                response = requests.get(
                    settings.PHOTON_URL,
                    params={"q": query, "limit": GEOCODE_CANDIDATES, "lang": "en", **US_CENTER},
                    timeout=8,
                    headers=settings.HTTP_HEADERS,
                )
                response.raise_for_status()
            except requests.RequestException:
                return _upstream_failure("Place search is unavailable right now.")
            results = _places(response.json().get("features", []))
            cache.set(cache_key, results, GEOCODE_CACHE_SECONDS)
        return Response({"results": results})


class PlanView(APIView):
    throttle_classes = [PlanThrottle]

    def post(self, request):
        serializer = PlanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trip = serializer.validated_data
        origin = (trip["origin"]["lat"], trip["origin"]["lon"])
        destination = (trip["destination"]["lat"], trip["destination"]["lon"])
        try:
            candidates = _routes_for(origin, destination, trip["load_lb"])
            if not candidates:
                return _upstream_failure("No drivable route was found between those places.")
            plan = plan_trip(candidates, trip["departure"], trip["load_lb"], trip["sample_interval_mi"])
        except RoutingError:
            return _upstream_failure("Couldn't find a route. Check both places are reachable by road.")
        except WeatherError:
            return _upstream_failure("The weather service is unavailable right now. Try again in a minute.")
        plan["origin"] = trip["origin"]
        plan["destination"] = trip["destination"]
        return Response(plan)


class HeatmapView(APIView):
    throttle_classes = [PlanThrottle]

    def post(self, request):
        serializer = HeatmapRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data
        routes = [[(lat, lon) for lon, lat in corridor] for corridor in body["corridors"]]
        try:
            return Response(build_heatmap(routes, body["departure"].timestamp(), body["load_lb"]))
        except WeatherError:
            return _upstream_failure("The weather service is unavailable right now. Try again in a minute.")
