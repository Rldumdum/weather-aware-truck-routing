import type { HeatmapField, Place, Plan, Route, TripRequest } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly retryable: boolean,
  ) {
    super(message);
  }
}

/** Flatten a DRF error body ({field: [messages]} or {detail}) into one sentence. */
function errorMessage(body: unknown): string {
  if (!body || typeof body !== "object") return "Something went wrong. Try again.";
  const values = Object.values(body as Record<string, unknown>);
  const first = values.flat(3)[0];
  if (typeof first === "string") return first;
  if (first && typeof first === "object") return errorMessage(first);
  return "Something went wrong. Try again.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (response.ok) return response.json();
  const body = await response.json().catch(() => null);
  throw new ApiError(errorMessage(body), response.status === 503 || response.status === 429);
}

function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}

export async function searchPlaces(query: string, signal?: AbortSignal): Promise<Place[]> {
  const params = new URLSearchParams({ q: query });
  const { results } = await request<{ results: Place[] }>(`/api/geocode?${params}`, { signal });
  return results;
}

export function fetchPlan(trip: TripRequest, signal?: AbortSignal): Promise<Plan> {
  return postJson("/api/plan", trip, signal);
}

export function fetchHeatmap(
  routes: Route[],
  departure: string,
  loadLb: number,
  signal?: AbortSignal,
): Promise<HeatmapField> {
  const corridors = routes.map((route) => route.corridor);
  return postJson("/api/heatmap", { corridors, departure, load_lb: loadLb }, signal);
}
