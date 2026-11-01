from datetime import datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase

from planner.analysis import (
    build_checkpoints,
    checkpoint_miles,
    plan_trip,
    recommend,
    segment_shares,
    summarize,
)
from planner.geo import Path
from planner.risk import Risk
from planner.routing import RouteCandidate
from planner.weather import HourlyForecast, cell_of

DEPARTURE = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
HOURS = 120


def steady_forecast(wind=5.0, rain=0.0, snow=0.0) -> HourlyForecast:
    start = int(DEPARTURE.timestamp())
    times = [start + hour * 3600 for hour in range(HOURS)]
    return HourlyForecast(times, [wind] * HOURS, [wind] * HOURS, [rain] * HOURS, [snow] * HOURS)


def straight_path(degrees=1.0, seconds=7200.0) -> Path:
    return Path([(40.0, -100.0), (40.0, -100.0 + degrees)], [0.0, seconds])


def route(name="A", duration=3600, average=1.0, **miles) -> dict:
    return {
        "id": name.lower(),
        "name": name,
        "duration_s": duration,
        "summary": {
            "miles_by_risk": {"low": 0.0, "moderate": 0.0, "high": 0.0, "severe": 0.0, "no_travel": 0.0, **miles},
            "average_risk": average,
        },
    }


class Sampling(SimpleTestCase):
    def test_spacing_includes_both_ends(self):
        self.assertEqual(checkpoint_miles(60, 25), [0, 25, 50, 60])

    def test_short_trip_still_has_start_and_end(self):
        self.assertEqual(checkpoint_miles(12, 50), [0, 12])

    def test_trip_on_an_exact_multiple_has_no_duplicate_end(self):
        self.assertEqual(checkpoint_miles(50, 25), [0, 25, 50])

    def test_shares_partition_the_route(self):
        miles = checkpoint_miles(137.5, 25)
        shares = segment_shares(miles)
        self.assertAlmostEqual(sum(shares), 137.5)
        self.assertEqual(shares[0], 12.5)
        self.assertEqual(shares[1], 25)


class Checkpoints(SimpleTestCase):
    def _lookup(self, path, forecast):
        cells = {cell_of((p.lat, p.lon)) for p in map(path.at_mile, checkpoint_miles(path.length_mi, 10))}
        return {cell: forecast for cell in cells}

    def test_eta_uses_router_time_at_each_checkpoint(self):
        path = straight_path()
        checkpoints = build_checkpoints(path, DEPARTURE, 20_000, 10, self._lookup(path, steady_forecast()), 0.1)
        self.assertEqual(checkpoints[0].eta, DEPARTURE)
        self.assertEqual(checkpoints[-1].eta, DEPARTURE + timedelta(seconds=7200))

    def test_weather_is_read_at_the_arrival_time_not_departure(self):
        path = straight_path(seconds=4 * 3600)
        forecast = steady_forecast()
        forecast.wind = [5.0] * 3 + [60.0] * (HOURS - 3)  # a front arrives three hours in
        checkpoints = build_checkpoints(path, DEPARTURE, 20_000, 10, self._lookup(path, forecast), 0.1)
        self.assertEqual(checkpoints[0].level, Risk.LOW)
        self.assertEqual(checkpoints[-1].level, Risk.NO_TRAVEL)

    def test_summary_miles_add_up_to_route_length(self):
        path = straight_path()
        checkpoints = build_checkpoints(path, DEPARTURE, 20_000, 10, self._lookup(path, steady_forecast()), 0.1)
        summary = summarize(checkpoints)
        self.assertAlmostEqual(sum(summary["miles_by_risk"].values()), path.length_mi, delta=0.2)

    def test_average_risk_is_weighted_by_distance(self):
        path = straight_path()
        forecast = steady_forecast(rain=0.3)  # High everywhere
        checkpoints = build_checkpoints(path, DEPARTURE, 20_000, 10, self._lookup(path, forecast), 0.1)
        self.assertEqual(summarize(checkpoints)["average_risk"], 2.0)


class Ranking(SimpleTestCase):
    def assertBest(self, routes, expected):
        self.assertEqual(recommend(routes)["route_id"], expected)

    def test_fewest_severe_miles_comes_first(self):
        self.assertBest([route("A", severe=25, high=0), route("B", severe=0, high=200)], "b")

    def test_high_miles_break_a_severe_tie(self):
        self.assertBest([route("A", severe=25, high=50), route("B", severe=25, high=25)], "b")

    def test_average_risk_breaks_a_high_tie(self):
        self.assertBest([route("A", average=0.9, high=25), route("B", average=0.4, high=25)], "b")

    def test_negligible_average_difference_lets_travel_time_decide(self):
        cleaner_but_slower = route("A", average=0.03, duration=11 * 3600)
        faster = route("B", average=0.07, duration=8 * 3600)
        self.assertBest([cleaner_but_slower, faster], "b")

    def test_meaningful_average_difference_beats_travel_time(self):
        self.assertBest([route("A", average=0.30, duration=8 * 3600), route("B", average=0.05, duration=11 * 3600)], "b")

    def test_tie_band_never_overrides_high_miles(self):
        self.assertBest([route("A", high=25, duration=3600), route("B", high=0, duration=36000)], "b")

    def test_travel_time_is_the_last_tie_break(self):
        self.assertBest([route("A", duration=9000), route("B", duration=8000)], "b")

    def test_risk_outranks_a_much_shorter_trip(self):
        self.assertBest([route("A", duration=3600, severe=10), route("B", duration=36000)], "b")

    def test_no_travel_loses_to_any_amount_of_severe(self):
        self.assertBest([route("A", no_travel=5), route("B", severe=300)], "b")

    def test_flags_when_every_route_is_blocked(self):
        result = recommend([route("A", no_travel=50), route("B", no_travel=25)])
        self.assertEqual(result["route_id"], "b")
        self.assertFalse(result["safe_to_travel"])
        self.assertIn("delay departure", result["headline"])

    def test_clear_route_is_safe_to_travel(self):
        self.assertTrue(recommend([route("A")])["safe_to_travel"])

    def test_reason_names_the_criterion_that_decided_it(self):
        reason = recommend([route("A", severe=50), route("B", severe=0)])["reason"]
        self.assertIn("Fewest Severe miles", reason)
        self.assertIn("Route A", reason)

    def test_time_reason_when_risk_ties(self):
        reason = recommend([route("A", duration=4800), route("B", duration=3600)])["reason"]
        self.assertIn("20 min faster", reason)

    def test_long_time_savings_read_in_hours(self):
        reason = recommend([route("A", duration=4 * 3600), route("B", duration=3600 - 720)])["reason"]
        self.assertIn("3h 12m faster", reason)

    def test_reason_quotes_average_risk_when_it_decided(self):
        reason = recommend([route("A", average=0.9), route("B", average=0.2)])["reason"]
        self.assertEqual(reason, "Lowest average risk: 0.20 vs 0.90 on Route A")

    def test_ranks_are_assigned_to_every_route(self):
        routes = [route("A", severe=10), route("B", high=10), route("C", high=20)]
        recommend(routes)
        self.assertEqual({r["name"]: r["rank"] for r in routes}, {"B": 1, "C": 2, "A": 3})


class FullPlan(SimpleTestCase):
    def test_plan_payload(self):
        candidate = RouteCandidate(straight_path(), ["I-80"], "valhalla-truck")

        def fake_forecasts(cells):
            return {cell: steady_forecast(wind=40.0) for cell in cells}

        with mock.patch("planner.analysis.forecasts_for", fake_forecasts):
            plan = plan_trip([candidate], DEPARTURE, 45_000, 25)
        only = plan["routes"][0]
        self.assertEqual(plan["recommendation"]["route_id"], "a")
        self.assertEqual(only["checkpoints"][0]["risk"], "Severe")  # 40 mph with over 40k lb
        self.assertTrue(only["checkpoints"][0]["load_adjusted"])
        self.assertEqual(only["checkpoints"][0]["drivers"], ["wind"])
        self.assertEqual(only["arrival"], "2026-11-01T14:00:00Z")
        self.assertEqual(len(only["geometry"]), len(only["geometry_seconds"]))
