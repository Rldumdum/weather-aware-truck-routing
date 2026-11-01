export interface Place {
  label: string;
  lat: number;
  lon: number;
}

export type SampleInterval = 10 | 25 | 50;

export interface TripRequest {
  origin: Place;
  destination: Place;
  departure: string;
  load_lb: number;
  sample_interval_mi: SampleInterval;
}

export type RiskKey = "low" | "moderate" | "high" | "severe" | "no_travel";

export interface Checkpoint {
  mile: number;
  lat: number;
  lon: number;
  eta: string;
  wind_mph: number;
  gust_mph: number;
  rain_in_hr: number;
  snow_in_hr: number;
  risk: string;
  risk_level: number;
  drivers: string[];
  load_adjusted: boolean;
  segment_miles: number;
}

export interface RouteSummary {
  miles_by_risk: Record<RiskKey, number>;
  average_risk: number;
  peak_risk: string;
}

export interface Route {
  id: string;
  name: string;
  roads: string[];
  source: "valhalla-truck" | "osrm-car";
  distance_mi: number;
  duration_s: number;
  arrival: string;
  rank: number;
  geometry: [number, number][];
  geometry_seconds: number[];
  corridor: [number, number][];
  summary: RouteSummary;
  checkpoints: Checkpoint[];
}

export interface Recommendation {
  route_id: string;
  headline: string;
  reason: string;
  safe_to_travel: boolean;
}

export interface Plan {
  origin: Place;
  destination: Place;
  departure: string;
  load_lb: number;
  sample_interval_mi: SampleInterval;
  weather_cell_deg: number;
  routes: Route[];
  recommendation: Recommendation;
}

export interface HeatmapField {
  width: number;
  height: number;
  frames: number;
  /** Mercator extent: west, north, east, south, each 0..1. */
  bounds: [number, number, number, number];
  locations: number;
  /** Base64 scores: frame, then row, then column, then wind/rain/snow. */
  data: string;
}

export type HeatMetric = "overall" | "wind" | "rain" | "snow";
