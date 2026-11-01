import { LngLatBounds, Map as MapLibreMap, Marker, NavigationControl, Popup, setWorkerUrl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";

import { formatDuration, formatEta, formatMiles } from "../format";
import { RISK_LEVELS } from "../risk";
import type { Checkpoint, HeatMetric, HeatmapField, Plan, Route } from "../types";
import { HeatLayer } from "./heatLayer";
import {
  CHECKPOINT_HIT_LAYER,
  ROUTE_HIT_LAYER,
  addRouteLayers,
  labelAnchor,
  setFocusedCheckpoint,
  setRouteData,
  setSelectedRoute,
  truckPosition,
} from "./routeLayers";

setWorkerUrl(workerUrl);

const BASEMAP = "https://tiles.openfreemap.org/styles/dark";
const INITIAL_VIEW = { center: [-98.5, 39.5] as [number, number], zoom: 3.6 };
const NARROW_WIDTH_PX = 700;
const FIT_PADDING = { top: 70, right: 80, bottom: 200, left: 70 };
const NARROW_FIT_PADDING = { top: 40, right: 30, bottom: 150, left: 30 };

export interface MapHandle {
  /** Called on every slider tick, so it must stay free of React state. */
  setHour(hour: number): void;
}

interface Props {
  ref: Ref<MapHandle>;
  plan: Plan | null;
  heatmap: HeatmapField | null;
  metric: HeatMetric;
  selectedRouteId: string | null;
  focusedCheckpoint: number | null;
  onSelectRoute(routeId: string): void;
}

function checkpointPopup(route: Route, checkpoint: Checkpoint): string {
  const level = RISK_LEVELS[checkpoint.risk_level];
  const because = checkpoint.drivers.length
    ? `Driven by ${checkpoint.drivers.join(" and ")}${checkpoint.load_adjusted ? ", raised by load weight" : ""}`
    : "All conditions within Low limits";
  return `
    <div class="popup-head">
      <span class="swatch" style="background:${level.color}"></span>
      <strong>${level.label}</strong>
      <span class="popup-route">Route ${route.name}, mile ${formatMiles(checkpoint.mile)}</span>
    </div>
    <div class="popup-eta">ETA ${formatEta(checkpoint.eta)}</div>
    <dl class="popup-grid">
      <dt>Wind</dt><dd>${checkpoint.wind_mph.toFixed(0)} mph</dd>
      <dt>Gusts</dt><dd>${checkpoint.gust_mph.toFixed(0)} mph</dd>
      <dt>Rain</dt><dd>${checkpoint.rain_in_hr.toFixed(2)} in/hr</dd>
      <dt>Snow</dt><dd>${checkpoint.snow_in_hr.toFixed(2)} in/hr</dd>
    </dl>
    <div class="popup-why">${because}</div>`;
}

function routeChip(route: Route, onSelect: () => void): HTMLButtonElement {
  const miles = route.summary.miles_by_risk;
  const worst = [...RISK_LEVELS].reverse().find((level) => miles[level.key] > 0 && level.key !== "low");
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "route-chip";
  chip.setAttribute("aria-label", `Select route ${route.name}`);
  const risk = worst
    ? `<span class="swatch" style="background:${worst.color}"></span>${formatMiles(miles[worst.key])} mi ${worst.label}`
    : `<span class="swatch" style="background:${RISK_LEVELS[0].color}"></span>All Low`;
  chip.innerHTML = `
    <span class="route-chip-name">${route.name}</span>
    <span class="route-chip-body">
      <span>${formatMiles(route.distance_mi)} mi, ${formatDuration(route.duration_s)}</span>
      <span class="route-chip-risk">${risk}</span>
      <span class="route-chip-eta">Arrives ${formatEta(route.arrival)}</span>
    </span>`;
  chip.addEventListener("click", (event) => {
    event.stopPropagation();
    onSelect();
  });
  return chip;
}

function truckElement(): HTMLDivElement {
  const element = document.createElement("div");
  element.className = "truck-marker";
  element.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
    <path fill="currentColor" d="M2 6h12v9H2zM15 9h4l3 3v3h-7zM6.5 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm11 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/>
  </svg>`;
  return element;
}

function pinElement(kind: "origin" | "destination"): HTMLDivElement {
  const element = document.createElement("div");
  element.className = `trip-pin trip-pin-${kind}`;
  return element;
}

export function MapView({
  ref,
  plan,
  heatmap,
  metric,
  selectedRouteId,
  focusedCheckpoint,
  onSelectRoute,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const heatRef = useRef<HeatLayer | null>(null);
  const truckRef = useRef<Marker | null>(null);
  const hourRef = useRef(0);
  const planRef = useRef<Plan | null>(plan);
  const selectedRouteRef = useRef<Route | null>(null);
  const onSelectRef = useRef(onSelectRoute);
  const fittedRoutes = useRef("");
  const [ready, setReady] = useState(false);

  planRef.current = plan;
  onSelectRef.current = onSelectRoute;
  selectedRouteRef.current = plan?.routes.find((route) => route.id === selectedRouteId) ?? null;

  function placeTruck(): void {
    const route = selectedRouteRef.current;
    if (route) truckRef.current?.setLngLat(truckPosition(route, hourRef.current * 3600));
  }

  useImperativeHandle(ref, () => ({
    setHour(hour: number) {
      hourRef.current = hour;
      heatRef.current?.setHour(hour);
      placeTruck();
    },
  }));

  useEffect(() => {
    const map = new MapLibreMap({
      container: containerRef.current!,
      style: BASEMAP,
      ...INITIAL_VIEW,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    map.setMissingStyleImageResolver((id) => {
      // The basemap references POI icons it doesn't ship; they aren't needed here.
      map.addImage(id, { width: 1, height: 1, data: new Uint8Array(4) });
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;

    map.on("load", () => {
      const heat = new HeatLayer();
      const firstLabel = map.getStyle().layers.find((layer) => layer.type === "symbol")?.id;
      map.addLayer(heat, firstLabel);
      heatRef.current = heat;
      addRouteLayers(map);

      const popup = new Popup({ closeButton: false, closeOnClick: false, offset: 12, maxWidth: "260px" });
      map.on("mousemove", CHECKPOINT_HIT_LAYER, (event) => {
        const feature = event.features?.[0];
        const route = planRef.current?.routes.find((r) => r.id === feature?.properties.route);
        if (!feature || !route) return;
        const checkpoint = route.checkpoints[feature.properties.index];
        map.getCanvas().style.cursor = "pointer";
        setFocusedCheckpoint(map, route.id, feature.properties.index);
        popup.setLngLat([checkpoint.lon, checkpoint.lat]).setHTML(checkpointPopup(route, checkpoint)).addTo(map);
      });
      map.on("mouseleave", CHECKPOINT_HIT_LAYER, () => {
        map.getCanvas().style.cursor = "";
        setFocusedCheckpoint(map, null, null);
        popup.remove();
      });
      map.on("click", ROUTE_HIT_LAYER, (event) => {
        const routeId = event.features?.[0]?.properties.route;
        if (routeId) onSelectRef.current(routeId);
      });
      map.on("mouseenter", ROUTE_HIT_LAYER, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", ROUTE_HIT_LAYER, () => {
        map.getCanvas().style.cursor = "";
      });
      setReady(true);
    });

    return () => {
      setReady(false);
      map.remove();
      mapRef.current = null;
      heatRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    setRouteData(map, plan);
    if (!plan) return;

    // Re-sampling the same routes must not undo the user's pan and zoom.
    const routesKey = plan.routes.map((route) => `${route.id}:${route.distance_mi}`).join();
    if (routesKey !== fittedRoutes.current) {
      fittedRoutes.current = routesKey;
      const bounds = new LngLatBounds();
      plan.routes.forEach((route) => route.geometry.forEach((point) => bounds.extend(point)));
      const narrow = map.getContainer().clientWidth < NARROW_WIDTH_PX;
      map.fitBounds(bounds, { padding: narrow ? NARROW_FIT_PADDING : FIT_PADDING, duration: 900, maxZoom: 10 });
    }

    const markers = [
      new Marker({ element: pinElement("origin") }).setLngLat([plan.origin.lon, plan.origin.lat]),
      new Marker({ element: pinElement("destination") }).setLngLat([plan.destination.lon, plan.destination.lat]),
      ...plan.routes.map((route) =>
        new Marker({
          element: routeChip(route, () => onSelectRef.current(route.id)),
          anchor: "bottom",
          offset: [0, -8],
        }).setLngLat(labelAnchor(route, plan.routes)),
      ),
    ];
    markers.forEach((marker) => marker.addTo(map));

    const truck = new Marker({ element: truckElement() })
      .setLngLat(plan.routes[0].geometry[0])
      .addTo(map);
    truckRef.current = truck;
    placeTruck();

    return () => {
      markers.forEach((marker) => marker.remove());
      truck.remove();
      truckRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    setSelectedRoute(map, selectedRouteId);
    placeTruck();
    document.querySelectorAll<HTMLElement>(".route-chip").forEach((chip) => {
      const label = chip.querySelector(".route-chip-name")?.textContent;
      const route = plan?.routes.find((r) => r.name === label);
      chip.classList.toggle("is-selected", route?.id === selectedRouteId);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRouteId, plan, ready]);

  useEffect(() => {
    if (ready) heatRef.current?.setField(heatmap, plan?.routes ?? []);
    // The mask is drawn from the same routes the field was built for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heatmap, ready]);

  useEffect(() => {
    if (ready) heatRef.current?.setMetric(metric);
  }, [metric, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (map && ready) setFocusedCheckpoint(map, selectedRouteId, focusedCheckpoint);
  }, [focusedCheckpoint, selectedRouteId, ready]);

  return <div ref={containerRef} className="map" aria-label="Route map" role="application" />;
}
