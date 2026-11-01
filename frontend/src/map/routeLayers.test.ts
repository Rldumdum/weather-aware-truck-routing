import { describe, expect, it } from "vitest";

import type { Checkpoint, Route } from "../types";
import { labelAnchor, segmentFeatures, truckPosition } from "./routeLayers";

const MILES_PER_DEGREE_LON_AT_EQUATOR = 69.09;

function checkpoint(mile: number, risk_level: number): Checkpoint {
  return {
    mile,
    lat: 0,
    lon: mile / MILES_PER_DEGREE_LON_AT_EQUATOR,
    eta: "2026-11-01T12:00:00Z",
    wind_mph: 0,
    gust_mph: 0,
    rain_in_hr: 0,
    snow_in_hr: 0,
    risk: "Low",
    risk_level,
    drivers: [],
    load_adjusted: false,
    segment_miles: 25,
  };
}

function route(overrides: Partial<Route> = {}): Route {
  return {
    id: "a",
    name: "A",
    roads: [],
    source: "valhalla-truck",
    distance_mi: 69.09,
    duration_s: 3600,
    arrival: "2026-11-01T13:00:00Z",
    rank: 1,
    // A single straight edge, like a long simplified highway.
    geometry: [
      [0, 0],
      [1, 0],
    ],
    geometry_seconds: [0, 3600],
    corridor: [],
    summary: {
      miles_by_risk: { low: 0, moderate: 0, high: 0, severe: 0, no_travel: 0 },
      average_risk: 0,
      peak_risk: "Low",
    },
    checkpoints: [checkpoint(0, 0), checkpoint(34.5, 3), checkpoint(69.09, 0)],
    ...overrides,
  };
}

describe("segmentFeatures", () => {
  it("splits a single long edge at the boundary miles", () => {
    const features = segmentFeatures(route());
    expect(features.map((feature) => feature.properties?.level)).toEqual([0, 3, 0]);
  });

  it("puts each boundary halfway between checkpoints", () => {
    const [first] = segmentFeatures(route());
    const line = first.geometry as GeoJSON.LineString;
    const boundaryLon = line.coordinates[line.coordinates.length - 1][0];
    expect(boundaryLon).toBeCloseTo(0.25, 2);
  });

  it("covers the route end to end with no gaps", () => {
    const features = segmentFeatures(route());
    const lines = features.map((feature) => (feature.geometry as GeoJSON.LineString).coordinates);
    expect(lines[0][0]).toEqual([0, 0]);
    expect(lines[2][lines[2].length - 1]).toEqual([1, 0]);
    expect(lines[1][0]).toEqual(lines[0][lines[0].length - 1]);
    expect(lines[2][0]).toEqual(lines[1][lines[1].length - 1]);
  });
});

describe("truckPosition", () => {
  it("interpolates along the route by drive time", () => {
    expect(truckPosition(route(), 1800)[0]).toBeCloseTo(0.5);
  });

  it("follows uneven speed, not distance", () => {
    const slowStart = route({
      geometry: [
        [0, 0],
        [1, 0],
        [2, 0],
      ],
      geometry_seconds: [0, 3000, 3600],
    });
    expect(truckPosition(slowStart, 3000)[0]).toBeCloseTo(1);
  });

  it("starts at the origin and holds at the destination", () => {
    expect(truckPosition(route(), 0)).toEqual([0, 0]);
    expect(truckPosition(route(), 99_999)).toEqual([1, 0]);
  });
});

describe("labelAnchor", () => {
  it("pins the label where the route is farthest from the others", () => {
    const checkpoints = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((i) => ({ ...checkpoint(i, 0), lon: i, lat: 0 }));
    const bulge = checkpoints.map((point, i) => ({ ...point, lat: i === 7 ? 2 : 0 }));
    const a = route({ id: "a", checkpoints: bulge });
    const b = route({ id: "b", checkpoints });
    expect(labelAnchor(a, [a, b])).toEqual([7, 2]);
  });
});
