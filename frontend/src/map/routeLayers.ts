import type { ExpressionSpecification, GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";

import { RISK_LEVELS } from "../risk";
import type { Plan, Route } from "../types";

const ROUTES_SOURCE = "routes";
const CHECKPOINTS_SOURCE = "checkpoints";

export const CHECKPOINT_HIT_LAYER = "checkpoints-hit";
export const ROUTE_HIT_LAYER = "routes-hit";

const RISK_COLOR: ExpressionSpecification = [
  "match",
  ["get", "level"],
  ...RISK_LEVELS.flatMap((level, index) => [index, level.color]),
  RISK_LEVELS[0].color,
] as unknown as ExpressionSpecification;

type LonLat = [number, number];

function distanceMi(a: LonLat, b: LonLat): number {
  const toRad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toRad;
  const dLon = (b[0] - a[0]) * toRad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a[1] * toRad) * Math.cos(b[1] * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * 3958.8 * Math.asin(Math.sqrt(h));
}

/**
 * Slice a route into stretches, each coloured by the checkpoint that speaks for it.
 * Simplified highways can run 30 miles between vertices, so edges are split at the
 * exact boundary mile rather than at the nearest vertex.
 */
export function segmentFeatures(route: Route): GeoJSON.Feature[] {
  const { checkpoints, geometry } = route;
  const features: GeoJSON.Feature[] = [];
  let cursor = geometry[0];
  let nextVertex = 1;
  let travelled = 0;

  checkpoints.forEach((checkpoint, index) => {
    const next = checkpoints[index + 1];
    const stopMile = next ? (checkpoint.mile + next.mile) / 2 : Infinity;
    const line: LonLat[] = [cursor];
    while (nextVertex < geometry.length) {
      const target = geometry[nextVertex];
      const span = distanceMi(cursor, target);
      if (travelled + span > stopMile) {
        const f = (stopMile - travelled) / span;
        cursor = [cursor[0] + (target[0] - cursor[0]) * f, cursor[1] + (target[1] - cursor[1]) * f];
        travelled = stopMile;
        line.push(cursor);
        break;
      }
      travelled += span;
      cursor = target;
      nextVertex += 1;
      line.push(cursor);
    }
    if (line.length > 1) {
      features.push({
        type: "Feature",
        properties: { route: route.id, level: checkpoint.risk_level },
        geometry: { type: "LineString", coordinates: line },
      });
    }
  });
  return features;
}

function checkpointFeatures(route: Route): GeoJSON.Feature[] {
  return route.checkpoints.map((checkpoint, index) => ({
    type: "Feature",
    id: index,
    properties: { route: route.id, level: checkpoint.risk_level, index },
    geometry: { type: "Point", coordinates: [checkpoint.lon, checkpoint.lat] },
  }));
}

const NO_ROUTE: ExpressionSpecification = ["==", ["get", "route"], ""];

export function addRouteLayers(map: MapLibreMap): void {
  const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
  map.addSource(ROUTES_SOURCE, { type: "geojson", data: empty });
  map.addSource(CHECKPOINTS_SOURCE, { type: "geojson", data: empty });

  const lineLayout = { "line-cap": "round", "line-join": "round" } as const;

  map.addLayer({
    id: "routes-other-casing",
    type: "line",
    source: ROUTES_SOURCE,
    filter: NO_ROUTE,
    layout: lineLayout,
    paint: { "line-color": "#0d0f12", "line-width": 5, "line-opacity": 0.7 },
  });
  map.addLayer({
    id: "routes-other",
    type: "line",
    source: ROUTES_SOURCE,
    filter: NO_ROUTE,
    layout: lineLayout,
    paint: { "line-color": RISK_COLOR, "line-width": 2.5, "line-opacity": 0.65 },
  });
  map.addLayer({
    id: "routes-selected-casing",
    type: "line",
    source: ROUTES_SOURCE,
    filter: NO_ROUTE,
    layout: lineLayout,
    paint: { "line-color": "#0d0f12", "line-width": 9 },
  });
  map.addLayer({
    id: "routes-selected",
    type: "line",
    source: ROUTES_SOURCE,
    filter: NO_ROUTE,
    layout: lineLayout,
    paint: { "line-color": RISK_COLOR, "line-width": 5 },
  });
  map.addLayer({
    id: ROUTE_HIT_LAYER,
    type: "line",
    source: ROUTES_SOURCE,
    layout: lineLayout,
    paint: { "line-color": "#000", "line-width": 16, "line-opacity": 0 },
  });

  map.addLayer({
    id: "checkpoints-other",
    type: "circle",
    source: CHECKPOINTS_SOURCE,
    filter: NO_ROUTE,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 1.5, 8, 3.5],
      "circle-color": RISK_COLOR,
      "circle-stroke-color": "#0d0f12",
      "circle-stroke-width": 1,
      "circle-opacity": 0.85,
    },
  });
  map.addLayer({
    id: "checkpoints-selected",
    type: "circle",
    source: CHECKPOINTS_SOURCE,
    filter: NO_ROUTE,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 2.5, 8, 5.5],
      "circle-color": RISK_COLOR,
      "circle-stroke-color": "#0d0f12",
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 4, 1, 8, 2],
    },
  });
  map.addLayer({
    id: "checkpoint-focus",
    type: "circle",
    source: CHECKPOINTS_SOURCE,
    filter: ["==", ["get", "index"], -1],
    paint: {
      "circle-radius": 9,
      "circle-color": RISK_COLOR,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2.5,
    },
  });
  map.addLayer({
    id: CHECKPOINT_HIT_LAYER,
    type: "circle",
    source: CHECKPOINTS_SOURCE,
    paint: { "circle-radius": 11, "circle-color": "#000", "circle-opacity": 0 },
  });
}

export function setRouteData(map: MapLibreMap, plan: Plan | null): void {
  const routes = plan?.routes ?? [];
  (map.getSource(ROUTES_SOURCE) as GeoJSONSource).setData({
    type: "FeatureCollection",
    features: routes.flatMap(segmentFeatures),
  });
  (map.getSource(CHECKPOINTS_SOURCE) as GeoJSONSource).setData({
    type: "FeatureCollection",
    features: routes.flatMap(checkpointFeatures),
  });
}

export function setSelectedRoute(map: MapLibreMap, routeId: string | null): void {
  const id = routeId ?? "";
  const selected: ExpressionSpecification = ["==", ["get", "route"], id];
  const others: ExpressionSpecification = ["!=", ["get", "route"], id];
  for (const layer of ["routes-selected-casing", "routes-selected", "checkpoints-selected"]) {
    map.setFilter(layer, selected);
  }
  for (const layer of ["routes-other-casing", "routes-other", "checkpoints-other"]) {
    map.setFilter(layer, others);
  }
}

export function setFocusedCheckpoint(map: MapLibreMap, routeId: string | null, index: number | null): void {
  map.setFilter("checkpoint-focus", [
    "all",
    ["==", ["get", "route"], routeId ?? ""],
    ["==", ["get", "index"], index ?? -1],
  ]);
}

/** Where to pin a route's label: the checkpoint farthest from the other routes. */
export function labelAnchor(route: Route, routes: Route[]): LonLat {
  const others = routes.filter((other) => other.id !== route.id);
  const stride = Math.max(1, Math.floor(route.checkpoints.length / 40));
  let best = route.checkpoints[Math.floor(route.checkpoints.length / 2)];
  let bestGap = -1;
  route.checkpoints.forEach((checkpoint, index) => {
    const edge = Math.min(index, route.checkpoints.length - 1 - index);
    if (index % stride || edge < route.checkpoints.length * 0.15) return;
    const here: LonLat = [checkpoint.lon, checkpoint.lat];
    let gap = Infinity;
    for (const other of others) {
      for (const point of other.checkpoints) {
        gap = Math.min(gap, distanceMi(here, [point.lon, point.lat]));
      }
    }
    if (gap > bestGap) {
      bestGap = gap;
      best = checkpoint;
    }
  });
  return [best.lon, best.lat];
}

/** Where the truck is, `seconds` after departure, holding at the destination. */
export function truckPosition(route: Route, seconds: number): LonLat {
  const times = route.geometry_seconds;
  if (seconds >= times[times.length - 1]) return route.geometry[route.geometry.length - 1];
  let low = 0;
  let high = times.length - 1;
  while (high - low > 1) {
    const middle = (low + high) >> 1;
    if (times[middle] <= seconds) low = middle;
    else high = middle;
  }
  const span = times[high] - times[low];
  const f = span ? (seconds - times[low]) / span : 0;
  const [lon1, lat1] = route.geometry[low];
  const [lon2, lat2] = route.geometry[high];
  return [lon1 + (lon2 - lon1) * f, lat1 + (lat2 - lat1) * f];
}
