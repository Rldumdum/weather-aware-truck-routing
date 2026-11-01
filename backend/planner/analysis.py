"""Turn a route into checkpoints, a risk summary and a recommendation."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .geo import Path, Point, Position, simplify_indices
from .risk import Risk, WeatherSample, assess
from .routing import RouteCandidate
from .weather import HourlyForecast, cell_of, cell_size_for, forecasts_for

ROUTE_NAMES = "ABC"
MAP_TOLERANCE_DEG = 0.0015  # about 500 feet, invisible at route-overview zoom
CORRIDOR_STEP_MI = 5.0
AVERAGE_RISK_TIE = 0.1  # set to 0 for a strictly lexicographic ranking


@dataclass
class Checkpoint:
    mile: float
    position: Position
    eta: datetime
    weather: WeatherSample
    level: Risk
    drivers: list[str]
    load_adjusted: bool
    segment_miles: float  # miles of route this checkpoint speaks for


def checkpoint_miles(length_mi: float, interval_mi: float) -> list[float]:
    """Every interval from the origin, plus the destination itself."""
    miles = [0.0]
    while miles[-1] + interval_mi < length_mi:
        miles.append(miles[-1] + interval_mi)
    miles.append(length_mi)
    return miles


def segment_shares(miles: list[float]) -> list[float]:
    """Miles each checkpoint represents: halfway to each neighbour.

    The shares add up to exactly the route length, so per-class mile totals are
    a true partition of the route.
    """
    edges = [miles[0]]
    edges += [(a + b) / 2 for a, b in zip(miles, miles[1:])]
    edges.append(miles[-1])
    return [b - a for a, b in zip(edges, edges[1:])]


def build_checkpoints(
    path: Path,
    departure: datetime,
    load_lb: float,
    interval_mi: float,
    lookup: dict[Point, HourlyForecast],
    cell_deg: float,
) -> list[Checkpoint]:
    miles = checkpoint_miles(path.length_mi, interval_mi)
    checkpoints = []
    for mile, share in zip(miles, segment_shares(miles)):
        position = path.at_mile(mile)
        eta = departure + timedelta(seconds=position.seconds)
        forecast = lookup[cell_of((position.lat, position.lon), cell_deg)]
        weather = forecast.sample(eta.timestamp())
        assessment = assess(weather, load_lb)
        checkpoints.append(
            Checkpoint(
                mile=mile,
                position=position,
                eta=eta,
                weather=weather,
                level=assessment.level,
                drivers=assessment.drivers,
                load_adjusted=assessment.load_adjusted,
                segment_miles=share,
            )
        )
    return checkpoints


def summarize(checkpoints: list[Checkpoint]) -> dict:
    miles_by_risk = {level.name.lower(): 0.0 for level in Risk}
    weighted = total = 0.0
    for checkpoint in checkpoints:
        miles_by_risk[checkpoint.level.name.lower()] += checkpoint.segment_miles
        weighted += checkpoint.level * checkpoint.segment_miles
        total += checkpoint.segment_miles
    return {
        "miles_by_risk": {name: round(miles, 1) for name, miles in miles_by_risk.items()},
        "average_risk": round(weighted / total, 3) if total else 0.0,
        "peak_risk": max(checkpoint.level for checkpoint in checkpoints).label,
    }


def _severity(route: dict) -> tuple[float, float, float]:
    """Criteria 1 and 2, with No Travel above everything.

    The brief does not rank No Travel, but a route the rules forbid cannot beat a
    route they allow, so it sorts ahead of Severe miles.
    """
    miles = route["summary"]["miles_by_risk"]
    return miles["no_travel"], miles["severe"], miles["high"]


def rank_routes(routes: list[dict]) -> list[dict]:
    """Best first: fewest No Travel, Severe, then High miles, lowest average risk, fastest.

    Average risk compares within AVERAGE_RISK_TIE. A strict comparison would let
    a 0.03 vs 0.07 difference, far below what a forecast can resolve, justify
    hours of extra driving, and travel time could never decide anything.
    """
    lowest_average: dict[tuple, float] = {}
    for route in routes:
        group = _severity(route)
        average = route["summary"]["average_risk"]
        lowest_average[group] = min(lowest_average.get(group, average), average)

    def key(route: dict) -> tuple:
        group = _severity(route)
        average = route["summary"]["average_risk"]
        tied = average - lowest_average[group] <= AVERAGE_RISK_TIE
        return (*group, 0.0 if tied else average, route["duration_s"])

    return sorted(routes, key=key)


def _format_time_saved(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes} min"


SEVERITY_CRITERIA = ("fewest No Travel miles", "fewest Severe miles", "fewest High miles")


def _deciding_reason(best: dict, runner_up: dict) -> str:
    """Name the first criterion that separates the winner from the runner-up."""
    ours, theirs = _severity(best), _severity(runner_up)
    for name, our_miles, their_miles in zip(SEVERITY_CRITERIA, ours, theirs):
        if our_miles != their_miles:
            return f"{name}: {our_miles:.0f} mi vs {their_miles:.0f} mi on Route {runner_up['name']}"
    our_average = best["summary"]["average_risk"]
    their_average = runner_up["summary"]["average_risk"]
    if their_average - our_average > AVERAGE_RISK_TIE:
        return f"lowest average risk: {our_average:.2f} vs {their_average:.2f} on Route {runner_up['name']}"
    saved = _format_time_saved(runner_up["duration_s"] - best["duration_s"])
    return f"weather risk is level with Route {runner_up['name']}, and it is {saved} faster"


def recommend(routes: list[dict]) -> dict:
    ranked = rank_routes(routes)
    best = ranked[0]
    for rank, route in enumerate(ranked, start=1):
        route["rank"] = rank
    travel_blocked = best["summary"]["miles_by_risk"]["no_travel"] > 0
    if travel_blocked:
        headline = f"No route is clear of No Travel conditions. Route {best['name']} is the least bad; delay departure."
    else:
        headline = f"Take Route {best['name']}."
    reason = _deciding_reason(best, ranked[1]) if len(ranked) > 1 else "only one route is available"
    return {
        "route_id": best["id"],
        "headline": headline,
        "reason": reason[0].upper() + reason[1:],
        "safe_to_travel": not travel_blocked,
    }


def _route_payload(
    index: int,
    candidate: RouteCandidate,
    checkpoints: list[Checkpoint],
    departure: datetime,
) -> dict:
    path = candidate.path
    keep = simplify_indices(path.points, MAP_TOLERANCE_DEG)
    name = ROUTE_NAMES[index]
    return {
        "id": name.lower(),
        "name": name,
        "roads": candidate.roads,
        "source": candidate.source,
        "distance_mi": round(path.length_mi, 1),
        "duration_s": round(path.duration_s),
        "arrival": _iso(departure + timedelta(seconds=path.duration_s)),
        "geometry": [[round(path.points[i][1], 5), round(path.points[i][0], 5)] for i in keep],
        "geometry_seconds": [round(path.seconds[i]) for i in keep],
        "corridor": [[round(lon, 3), round(lat, 3)] for lat, lon in path.resampled(CORRIDOR_STEP_MI)],
        "summary": summarize(checkpoints),
        "checkpoints": [_checkpoint_payload(checkpoint) for checkpoint in checkpoints],
    }


def _checkpoint_payload(checkpoint: Checkpoint) -> dict:
    return {
        "mile": round(checkpoint.mile, 1),
        "lat": round(checkpoint.position.lat, 5),
        "lon": round(checkpoint.position.lon, 5),
        "eta": _iso(checkpoint.eta),
        "wind_mph": round(checkpoint.weather.wind_mph, 1),
        "gust_mph": round(checkpoint.weather.gust_mph, 1),
        "rain_in_hr": round(checkpoint.weather.rain_in_hr, 3),
        "snow_in_hr": round(checkpoint.weather.snow_in_hr, 3),
        "risk": checkpoint.level.label,
        "risk_level": int(checkpoint.level),
        "drivers": checkpoint.drivers,
        "load_adjusted": checkpoint.load_adjusted,
        "segment_miles": round(checkpoint.segment_miles, 1),
    }


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def plan_trip(
    candidates: list[RouteCandidate],
    departure: datetime,
    load_lb: float,
    interval_mi: float,
) -> dict:
    sample_points = [
        (position.lat, position.lon)
        for candidate in candidates
        for position in map(candidate.path.at_mile, checkpoint_miles(candidate.path.length_mi, interval_mi))
    ]
    cell_deg = cell_size_for(sample_points)
    lookup = forecasts_for({cell_of(point, cell_deg) for point in sample_points})

    routes = [
        _route_payload(
            index,
            candidate,
            build_checkpoints(candidate.path, departure, load_lb, interval_mi, lookup, cell_deg),
            departure,
        )
        for index, candidate in enumerate(candidates)
    ]
    return {
        "departure": _iso(departure),
        "load_lb": load_lb,
        "sample_interval_mi": interval_mi,
        "weather_cell_deg": cell_deg,
        "routes": routes,
        "recommendation": recommend(routes),
    }
