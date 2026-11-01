from django.test import SimpleTestCase

from planner.risk import (
    Risk,
    WeatherSample,
    assess,
    base_wind_risk,
    rain_risk,
    rain_score,
    snow_risk,
    snow_score,
    wind_risk,
    wind_score,
)

LIGHT_LOAD = 10_000


def calm(**overrides) -> WeatherSample:
    values = {"wind_mph": 0, "gust_mph": 0, "rain_in_hr": 0, "snow_in_hr": 0}
    return WeatherSample(**{**values, **overrides})


class WindThresholds(SimpleTestCase):
    def test_every_band_edge(self):
        cases = [
            (0, Risk.LOW), (24.9, Risk.LOW),
            (25, Risk.MODERATE), (34, Risk.MODERATE), (34.9, Risk.MODERATE),
            (35, Risk.HIGH), (44, Risk.HIGH), (44.9, Risk.HIGH),
            (45, Risk.SEVERE), (54, Risk.SEVERE), (54.9, Risk.SEVERE),
            (55, Risk.NO_TRAVEL), (80, Risk.NO_TRAVEL),
        ]
        for mph, expected in cases:
            with self.subTest(mph=mph):
                self.assertEqual(base_wind_risk(mph), expected)


class RainThresholds(SimpleTestCase):
    def test_every_band_edge(self):
        cases = [
            (0, Risk.LOW), (0.09, Risk.LOW),
            (0.10, Risk.MODERATE), (0.24, Risk.MODERATE),
            (0.25, Risk.HIGH), (0.49, Risk.HIGH),
            (0.50, Risk.SEVERE), (1.00, Risk.SEVERE),
            (1.01, Risk.NO_TRAVEL),
        ]
        for rate, expected in cases:
            with self.subTest(rate=rate):
                self.assertEqual(rain_risk(rate), expected)


class SnowThresholds(SimpleTestCase):
    def test_every_band_edge(self):
        cases = [
            (0, Risk.LOW), (0.49, Risk.LOW),
            (0.5, Risk.MODERATE), (0.99, Risk.MODERATE),
            (1.0, Risk.HIGH), (1.99, Risk.HIGH),
            (2.0, Risk.SEVERE), (3.0, Risk.SEVERE),
            (3.01, Risk.NO_TRAVEL),
        ]
        for rate, expected in cases:
            with self.subTest(rate=rate):
                self.assertEqual(snow_risk(rate), expected)


class LoadRules(SimpleTestCase):
    def test_55_mph_is_no_travel_for_any_load(self):
        for load in (1, 10_000, 30_000, 80_000):
            self.assertEqual(wind_risk(55, load), Risk.NO_TRAVEL)

    def test_45_to_54_with_over_30k_is_no_travel(self):
        self.assertEqual(wind_risk(45, 30_001), Risk.NO_TRAVEL)
        self.assertEqual(wind_risk(54.9, 30_001), Risk.NO_TRAVEL)

    def test_45_to_54_at_exactly_30k_stays_severe(self):
        self.assertEqual(wind_risk(50, 30_000), Risk.SEVERE)

    def test_35_to_44_with_over_40k_is_severe(self):
        self.assertEqual(wind_risk(35, 40_001), Risk.SEVERE)
        self.assertEqual(wind_risk(44.9, 40_001), Risk.SEVERE)

    def test_35_to_44_at_exactly_40k_stays_high(self):
        self.assertEqual(wind_risk(40, 40_000), Risk.HIGH)

    def test_load_rules_leave_other_wind_bands_alone(self):
        self.assertEqual(wind_risk(34.9, 80_000), Risk.MODERATE)
        self.assertEqual(wind_risk(10, 80_000), Risk.LOW)


class OverallAssessment(SimpleTestCase):
    def test_worst_condition_wins(self):
        result = assess(calm(wind_mph=30, rain_in_hr=0.3, snow_in_hr=2.5), LIGHT_LOAD)
        self.assertEqual(result.level, Risk.SEVERE)
        self.assertEqual(result.drivers, ["snow"])

    def test_ties_name_every_driver(self):
        result = assess(calm(wind_mph=40, rain_in_hr=0.3), LIGHT_LOAD)
        self.assertEqual(result.level, Risk.HIGH)
        self.assertEqual(result.drivers, ["wind", "rain"])

    def test_calm_weather_has_no_drivers(self):
        self.assertEqual(assess(calm(), LIGHT_LOAD).drivers, [])

    def test_flags_when_load_raised_the_class(self):
        self.assertTrue(assess(calm(wind_mph=40), 45_000).load_adjusted)
        self.assertFalse(assess(calm(wind_mph=40), LIGHT_LOAD).load_adjusted)

    def test_gusts_do_not_change_the_class(self):
        self.assertEqual(assess(calm(gust_mph=70), LIGHT_LOAD).level, Risk.LOW)


class HeatmapScores(SimpleTestCase):
    """floor(score) must equal the class, or the map would contradict the checkpoints."""

    def test_wind_score_agrees_with_class(self):
        for load in (10_000, 35_000, 45_000):
            for tenths in range(0, 800):
                mph = tenths / 10
                with self.subTest(mph=mph, load=load):
                    self.assertEqual(int(wind_score(mph, load)), wind_risk(mph, load))

    def test_rain_score_agrees_with_class(self):
        for hundredths in range(0, 200):
            rate = hundredths / 100
            self.assertEqual(int(rain_score(rate)), rain_risk(rate), rate)

    def test_snow_score_agrees_with_class(self):
        for tenths in range(0, 50):
            rate = tenths / 10
            self.assertEqual(int(snow_score(rate)), snow_risk(rate), rate)

    def test_score_rises_inside_a_band(self):
        self.assertLess(wind_score(5, LIGHT_LOAD), wind_score(20, LIGHT_LOAD))
