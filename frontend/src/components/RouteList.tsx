import { formatDuration, formatEta, formatMiles } from "../format";
import { RISK_LEVELS, riskColor } from "../risk";
import type { Plan, Route } from "../types";

/** Consecutive checkpoints of the same class, merged into one stretch of the strip. */
function riskRuns(route: Route): { level: number; miles: number; startMile: number }[] {
  const runs: { level: number; miles: number; startMile: number }[] = [];
  for (const checkpoint of route.checkpoints) {
    const last = runs[runs.length - 1];
    if (last?.level === checkpoint.risk_level) last.miles += checkpoint.segment_miles;
    else runs.push({ level: checkpoint.risk_level, miles: checkpoint.segment_miles, startMile: checkpoint.mile });
  }
  return runs;
}

function RiskStrip({ route }: { route: Route }) {
  return (
    <div className="risk-strip" role="img" aria-label={`Risk along Route ${route.name}, origin to destination`}>
      {riskRuns(route).map((run) => (
        <span key={run.startMile} style={{ flexGrow: run.miles, background: riskColor(run.level) }} />
      ))}
    </div>
  );
}

function RiskMiles({ route }: { route: Route }) {
  const miles = route.summary.miles_by_risk;
  const rows = RISK_LEVELS.filter((level) => level.key !== "low" && miles[level.key] > 0).reverse();
  if (!rows.length) {
    return <p className="risk-miles-empty">Low risk the whole way</p>;
  }
  return (
    <ul className="risk-miles">
      {rows.map((level) => (
        <li key={level.key}>
          <span className="swatch" style={{ background: level.color }} />
          <span className="num">{formatMiles(miles[level.key])} mi</span> {level.label}
        </li>
      ))}
    </ul>
  );
}

interface Props {
  plan: Plan;
  selectedRouteId: string | null;
  onSelect(routeId: string): void;
}

export function RouteList({ plan, selectedRouteId, onSelect }: Props) {
  const ranked = [...plan.routes].sort((a, b) => a.rank - b.rank);
  return (
    <ol className="route-list">
      {ranked.map((route) => {
        const recommended = route.id === plan.recommendation.route_id;
        const selected = route.id === selectedRouteId;
        return (
          <li key={route.id}>
            <button
              type="button"
              className={`route-card${selected ? " is-selected" : ""}`}
              aria-pressed={selected}
              onClick={() => onSelect(route.id)}
            >
              <span className="route-card-head">
                <span className="route-letter">{route.name}</span>
                <span className="route-roads">{route.roads.join(", ") || "Local roads"}</span>
                {recommended && (
                  <span className={`badge${plan.recommendation.safe_to_travel ? "" : " badge-warn"}`}>
                    {plan.recommendation.safe_to_travel ? "Recommended" : "Least bad"}
                  </span>
                )}
              </span>
              <span className="route-stats">
                <span>
                  <b className="num">{formatDuration(route.duration_s)}</b>
                  <small>drive time</small>
                </span>
                <span>
                  <b className="num">{formatMiles(route.distance_mi)}</b>
                  <small>miles</small>
                </span>
                <span>
                  <b className="num">{route.summary.average_risk.toFixed(2)}</b>
                  <small>avg risk, 0–4</small>
                </span>
                <span>
                  <b className="num">{formatEta(route.arrival)}</b>
                  <small>arrival</small>
                </span>
              </span>
              <RiskStrip route={route} />
              <RiskMiles route={route} />
            </button>
          </li>
        );
      })}
    </ol>
  );
}
