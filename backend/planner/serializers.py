from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from .geo import haversine_mi

MAX_LOAD_LB = 120_000
MAX_DEPARTURE_DAYS = 7
SAMPLE_INTERVALS = (10, 25, 50)
MIN_TRIP_MI = 1.0


def validate_departure(value):
    now = timezone.now()
    if value < now - timedelta(hours=1):
        raise serializers.ValidationError("Departure can't be in the past.")
    if value > now + timedelta(days=MAX_DEPARTURE_DAYS):
        raise serializers.ValidationError(f"Forecasts only cover departures up to {MAX_DEPARTURE_DAYS} days ahead.")
    return value


class PlaceSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=200)
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lon = serializers.FloatField(min_value=-180, max_value=180)


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceSerializer()
    destination = PlaceSerializer()
    departure = serializers.DateTimeField(validators=[validate_departure])
    load_lb = serializers.FloatField(min_value=1, max_value=MAX_LOAD_LB)
    sample_interval_mi = serializers.ChoiceField(choices=SAMPLE_INTERVALS, default=25)

    def validate(self, attrs):
        origin, destination = attrs["origin"], attrs["destination"]
        straight_line = haversine_mi((origin["lat"], origin["lon"]), (destination["lat"], destination["lon"]))
        if straight_line < MIN_TRIP_MI:
            raise serializers.ValidationError("Origin and destination must be different places.")
        return attrs


class CorridorField(serializers.ListField):
    """A route as [lon, lat] pairs, the GeoJSON order the frontend already holds."""

    child = serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2)
    min_length = 2
    max_length = 2000


class HeatmapRequestSerializer(serializers.Serializer):
    corridors = serializers.ListField(child=CorridorField(), min_length=1, max_length=3)
    departure = serializers.DateTimeField(validators=[validate_departure])
    load_lb = serializers.FloatField(min_value=1, max_value=MAX_LOAD_LB)
