"""Forecast field for the corridor heatmap.

The browser gets one compact byte array and animates it on the GPU, so moving
the 0-48 hour slider never touches the network. The grid is regular in mercator
space, which is what lets the shader map texture coordinates straight onto the
map with no per-pixel reprojection.

Each texel holds three scores (wind, rain, snow) produced by the same rule code
as the checkpoints. No threshold is duplicated in the frontend.
"""

import base64

from .geo import Point, inverse_mercator, mercator, miles_per_mercator_unit
from .risk import rain_score, snow_score, wind_score
from .weather import cell_of, forecasts_for

FRAMES = 49  # hours 0 through 48 inclusive
CORRIDOR_RADIUS_MI = 35.0
MIN_SPACING_MI = 8.0
MAX_POINTS = 240  # Open-Meteo locations per heatmap
MAX_SIDE = 128
EDGE_FILL_CELLS = 3  # fill this far past the corridor so GPU filtering has clean edges
SCORE_TO_BYTE = 255 / 4


def _corridor_cells(routes_xy, x0, y0, spacing, radius, width, height) -> set[tuple[int, int]]:
    """Grid cells whose center lies within radius of any route."""
    reach = int(radius / spacing) + 1
    limit = max(radius / spacing, 0.75) ** 2  # never skip the cell a route passes through
    cells = set()
    for route in routes_xy:
        for x, y in route:
            fx, fy = (x - x0) / spacing, (y - y0) / spacing
            col, row = int(fx), int(fy)
            for c in range(max(col - reach, 0), min(col + reach + 1, width)):
                for r in range(max(row - reach, 0), min(row + reach + 1, height)):
                    if (c + 0.5 - fx) ** 2 + (r + 0.5 - fy) ** 2 <= limit:
                        cells.add((c, r))
    return cells


def layout_grid(routes: list[list[Point]]) -> dict:
    """Pick the finest grid whose corridor stays within MAX_POINTS."""
    routes_xy = [[mercator(lat, lon) for lat, lon in route] for route in routes]
    all_xy = [xy for route in routes_xy for xy in route]
    mid_lat = sum(lat for route in routes for lat, _ in route) / sum(map(len, routes))
    mi_per_unit = miles_per_mercator_unit(mid_lat)
    radius = (CORRIDOR_RADIUS_MI + MIN_SPACING_MI) / mi_per_unit

    x0 = min(x for x, _ in all_xy) - radius
    x1 = max(x for x, _ in all_xy) + radius
    y0 = min(y for _, y in all_xy) - radius
    y1 = max(y for _, y in all_xy) + radius

    spacing = max(MIN_SPACING_MI / mi_per_unit, max(x1 - x0, y1 - y0) / MAX_SIDE)
    while True:
        width = int((x1 - x0) / spacing) + 1
        height = int((y1 - y0) / spacing) + 1
        cells = _corridor_cells(routes_xy, x0, y0, spacing, CORRIDOR_RADIUS_MI / mi_per_unit, width, height)
        if len(cells) <= MAX_POINTS:
            break
        spacing *= 1.1
    return {
        "width": width,
        "height": height,
        "x0": x0,
        "y0": y0,
        "spacing": spacing,
        "cells": cells,
    }


def _neighbours(cell: tuple[int, int]) -> list[tuple[int, int]]:
    col, row = cell
    return [(col + dx, row + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]


def _fill_margin(scored: dict[tuple[int, int], list], width: int, height: int) -> None:
    """Extend the field past the corridor so GPU filtering has values to read.

    Each margin cell averages its known neighbours, layer by layer. Copying the
    nearest cell instead would smear any edge value outward into visible streaks.
    """
    for _ in range(EDGE_FILL_CELLS):
        margin = {
            neighbour
            for cell in scored
            for neighbour in _neighbours(cell)
            if neighbour not in scored and 0 <= neighbour[0] < width and 0 <= neighbour[1] < height
        }
        filled = {}
        for cell in margin:
            known = [scored[neighbour] for neighbour in _neighbours(cell) if neighbour in scored]
            filled[cell] = [
                tuple(sum(channel) // len(known) for channel in zip(*hour_values))
                for hour_values in zip(*known)
            ]
        scored.update(filled)


def build_heatmap(routes: list[list[Point]], departure_ts: float, load_lb: float) -> dict:
    grid = layout_grid(routes)
    width, height, spacing = grid["width"], grid["height"], grid["spacing"]
    x0, y0 = grid["x0"], grid["y0"]

    points = {
        cell: inverse_mercator(x0 + (cell[0] + 0.5) * spacing, y0 + (cell[1] + 0.5) * spacing)
        for cell in grid["cells"]
    }
    lookup = forecasts_for({cell_of(point) for point in points.values()})

    scored = {}
    for cell, point in points.items():
        forecast = lookup[cell_of(point)]
        frames = []
        for hour in range(FRAMES):
            weather = forecast.sample(departure_ts + hour * 3600)
            frames.append(
                (
                    round(wind_score(weather.wind_mph, load_lb) * SCORE_TO_BYTE),
                    round(rain_score(weather.rain_in_hr) * SCORE_TO_BYTE),
                    round(snow_score(weather.snow_in_hr) * SCORE_TO_BYTE),
                )
            )
        scored[cell] = frames

    _fill_margin(scored, width, height)
    empty = [(0, 0, 0)] * FRAMES
    data = bytearray()
    for hour in range(FRAMES):
        for row in range(height):
            for col in range(width):
                data.extend(scored.get((col, row), empty)[hour])

    return {
        "width": width,
        "height": height,
        "frames": FRAMES,
        "bounds": [x0, y0, x0 + width * spacing, y0 + height * spacing],
        "locations": len(points),
        "data": base64.b64encode(bytes(data)).decode("ascii"),
    }
