import base64
from unittest import mock

from django.test import SimpleTestCase

from planner.heatmap import (
    EDGE_FILL_CELLS,
    FRAMES,
    MAX_POINTS,
    MAX_SIDE,
    SCORE_TO_BYTE,
    _fill_margin,
    build_heatmap,
    layout_grid,
)
from planner.weather import HourlyForecast

ROUTE = [(40.0, -105.0 + i * 0.1) for i in range(60)]
START = 1_800_000_000


def rising_wind_forecast() -> HourlyForecast:
    """Wind in mph equals hours after START, so a frame's value identifies its hour."""
    hours = range(60)
    wind = [float(hour) for hour in hours]
    zeros = [0.0 for _ in hours]
    return HourlyForecast([START + h * 3600 for h in hours], wind, wind, zeros, zeros)


def fake_forecasts(cells):
    return {cell: rising_wind_forecast() for cell in cells}


class Grid(SimpleTestCase):
    def test_short_trip_stays_within_budget(self):
        self.assertLessEqual(len(layout_grid([ROUTE])["cells"]), MAX_POINTS)

    def test_cross_country_trip_stays_within_budget(self):
        long_route = [(35.0 + i * 0.1, -120.0 + i * 0.4) for i in range(120)]
        grid = layout_grid([long_route])
        self.assertLessEqual(len(grid["cells"]), MAX_POINTS)
        self.assertLessEqual(max(grid["width"], grid["height"]), MAX_SIDE + 1)

    def test_long_trip_uses_most_of_its_budget(self):
        long_route = [(35.0 + i * 0.1, -120.0 + i * 0.4) for i in range(120)]
        self.assertGreater(len(layout_grid([long_route])["cells"]), MAX_POINTS * 0.7)


class Margin(SimpleTestCase):
    def column(self):
        """A corridor edge along row 0: one spike with calm cells either side."""
        spike = [(200, 0, 0)]
        calm = [(0, 0, 0)]
        scored = {(col, 0): (spike if col == 5 else calm) for col in range(11)}
        _fill_margin(scored, 11, 10)
        return [scored[(5, row)][0][0] for row in range(EDGE_FILL_CELLS + 1)]

    def test_an_edge_spike_fades_instead_of_streaking(self):
        values = self.column()
        self.assertEqual(values[0], 200)
        self.assertLess(values[1], 100)  # copying the nearest cell would keep 200 all the way out
        self.assertTrue(all(a >= b for a, b in zip(values, values[1:])), values)

    def test_margin_stops_at_the_fill_depth_and_grid_edge(self):
        scored = {(0, 0): [(10, 10, 10)]}
        _fill_margin(scored, 2, 20)
        self.assertEqual(max(row for _, row in scored), EDGE_FILL_CELLS)
        self.assertEqual(max(col for col, _ in scored), 1)


class Field(SimpleTestCase):
    def build(self):
        with mock.patch("planner.heatmap.forecasts_for", fake_forecasts):
            return build_heatmap([ROUTE], START, 20_000)

    def test_byte_layout_matches_the_declared_shape(self):
        result = self.build()
        raw = base64.b64decode(result["data"])
        self.assertEqual(len(raw), result["width"] * result["height"] * FRAMES * 3)

    def test_each_frame_reads_its_own_hour(self):
        result = self.build()
        raw = base64.b64decode(result["data"])
        frame_size = result["width"] * result["height"] * 3
        self.assertEqual(max(raw[0:frame_size:3]), 0)  # 0 mph at hour 0
        # 48 mph at hour 48: Severe, 30% through the 45-55 band.
        self.assertEqual(max(raw[frame_size * 48 : frame_size * 49 : 3]), round(3.3 * SCORE_TO_BYTE))

    def test_bounds_run_west_to_east_and_north_to_south(self):
        west, north, east, south = self.build()["bounds"]
        self.assertLess(west, east)
        self.assertLess(north, south)
