import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, fetchHeatmap, fetchPlan } from "./api";
import { CheckpointTable } from "./components/CheckpointTable";
import { RouteList } from "./components/RouteList";
import { Timeline, type HeatStatus } from "./components/Timeline";
import { TripForm } from "./components/TripForm";
import { MapView, type MapHandle } from "./map/MapView";
import type { HeatMetric, HeatmapField, Plan, SampleInterval, TripRequest } from "./types";

const HEATMAP_RETRY_MS = 15_000;
const HEATMAP_MAX_TRIES = 3;

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });
}

/** The same routes, departure and load give the same field, whatever the sampling. */
function heatmapKey(plan: Plan): string {
  const places = [plan.origin, plan.destination].map((place) => `${place.lat},${place.lon}`);
  return [...places, plan.departure, plan.load_lb].join("|");
}

export function App() {
  const [trip, setTrip] = useState<TripRequest | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);
  const [focusedCheckpoint, setFocusedCheckpoint] = useState<number | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapField | null>(null);
  const [heatStatus, setHeatStatus] = useState<HeatStatus>("idle");
  const [metric, setMetric] = useState<HeatMetric>("overall");

  const mapRef = useRef<MapHandle>(null);
  const planController = useRef<AbortController | null>(null);

  const runPlan = useCallback(async (request: TripRequest) => {
    planController.current?.abort();
    const controller = new AbortController();
    planController.current = controller;
    setTrip(request);
    setBusy(true);
    setError(null);
    try {
      const result = await fetchPlan(request, controller.signal);
      setPlan(result);
      setSelectedRouteId(result.recommendation.route_id);
      setFocusedCheckpoint(null);
    } catch (failure) {
      if (isAbort(failure)) return;
      setError(failure instanceof Error ? failure.message : "Something went wrong. Try again.");
    } finally {
      if (planController.current === controller) setBusy(false);
    }
  }, []);

  function changeInterval(interval: SampleInterval) {
    if (trip) runPlan({ ...trip, sample_interval_mi: interval });
  }

  const fieldKey = plan ? heatmapKey(plan) : null;
  const planForField = useRef(plan);
  planForField.current = plan;

  useEffect(() => {
    const current = planForField.current;
    if (!current) return;
    const controller = new AbortController();
    setHeatmap(null);
    setHeatStatus("loading");

    (async () => {
      for (let attempt = 1; ; attempt++) {
        try {
          const field = await fetchHeatmap(current.routes, current.departure, current.load_lb, controller.signal);
          setHeatmap(field);
          setHeatStatus("ready");
          return;
        } catch (failure) {
          if (isAbort(failure)) return;
          // The forecast quota refills each minute, so a short wait usually clears it.
          const retryable = failure instanceof ApiError && failure.retryable;
          if (!retryable || attempt === HEATMAP_MAX_TRIES) {
            setHeatStatus("failed");
            return;
          }
          await wait(HEATMAP_RETRY_MS, controller.signal).catch(() => undefined);
          if (controller.signal.aborted) return;
        }
      }
    })();

    return () => controller.abort();
  }, [fieldKey]);

  const selectedRoute = useMemo(
    () => plan?.routes.find((route) => route.id === selectedRouteId) ?? null,
    [plan, selectedRouteId],
  );

  const setHour = useCallback((hour: number) => mapRef.current?.setHour(hour), []);

  const usesCarRouting = plan?.routes.some((route) => route.source === "osrm-car");

  return (
    <div className="app">
      <aside className="panel">
        <header className="panel-header">
          <h1>Weather-Aware Truck Routing</h1>
          <p>Compare routes by forecast conditions at the time the truck reaches each mile.</p>
        </header>

        <TripForm busy={busy} onSubmit={runPlan} onIntervalChange={changeInterval} />

        {error && (
          <p className="notice notice-error" role="alert">
            {error}
          </p>
        )}

        {plan && (
          <div className="results">
            <section
              className={`recommendation${plan.recommendation.safe_to_travel ? "" : " is-blocked"}`}
              aria-live="polite"
            >
              <h2>{plan.recommendation.headline}</h2>
              <p>{plan.recommendation.reason}.</p>
            </section>
            {usesCarRouting && (
              <p className="notice">
                This trip exceeds the truck router's 930-mile limit, so roads and drive times come from a car profile.
              </p>
            )}
            <RouteList plan={plan} selectedRouteId={selectedRouteId} onSelect={setSelectedRouteId} />
            {selectedRoute && <CheckpointTable route={selectedRoute} onFocus={setFocusedCheckpoint} />}
          </div>
        )}
      </aside>

      <main className="map-stage">
        <MapView
          ref={mapRef}
          plan={plan}
          heatmap={heatmap}
          metric={metric}
          selectedRouteId={selectedRouteId}
          focusedCheckpoint={focusedCheckpoint}
          onSelectRoute={setSelectedRouteId}
        />
        {plan && (
          <Timeline
            departure={plan.departure}
            route={selectedRoute}
            status={heatStatus}
            metric={metric}
            onMetricChange={setMetric}
            onHour={setHour}
          />
        )}
        {!plan && !busy && (
          <p className="map-hint">Enter a trip and compare routes to see the forecast along the corridor.</p>
        )}
      </main>
    </div>
  );
}
