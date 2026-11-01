import type { RiskKey } from "./types";

/** Risk classes in order. Palette checked with a CVD validator: every use is also labelled in text. */
export const RISK_LEVELS: { key: RiskKey; label: string; color: string }[] = [
  { key: "low", label: "Low", color: "#2eb872" },
  { key: "moderate", label: "Moderate", color: "#fad619" },
  { key: "high", label: "High", color: "#f28a3c" },
  { key: "severe", label: "Severe", color: "#e0383e" },
  { key: "no_travel", label: "No Travel", color: "#c04fe8" },
];

export function riskColor(level: number): string {
  return RISK_LEVELS[level].color;
}
