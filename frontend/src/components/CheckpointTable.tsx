import { formatEta, formatMiles, timeZoneName } from "../format";
import { riskColor } from "../risk";
import type { Route } from "../types";

interface Props {
  route: Route;
  onFocus(index: number | null): void;
}

export function CheckpointTable({ route, onFocus }: Props) {
  return (
    <section className="checkpoints" aria-label={`Checkpoints on Route ${route.name}`}>
      <h2>
        Route {route.name} checkpoints <span>{route.checkpoints.length}</span>
      </h2>
      <div className="table-scroll" onMouseLeave={() => onFocus(null)}>
        <table>
          <thead>
            <tr>
              <th scope="col" className="r">Mile</th>
              <th scope="col">ETA ({timeZoneName()})</th>
              <th scope="col" className="r" title="Sustained wind, mph">Wind</th>
              <th scope="col" className="r" title="Rain, inches per hour">Rain</th>
              <th scope="col" className="r" title="Snow, inches per hour">Snow</th>
              <th scope="col">Risk</th>
            </tr>
          </thead>
          <tbody>
            {route.checkpoints.map((checkpoint, index) => (
              <tr key={checkpoint.mile} onMouseEnter={() => onFocus(index)}>
                <td className="r num">{formatMiles(checkpoint.mile)}</td>
                <td className="num">{formatEta(checkpoint.eta)}</td>
                <td className="r num">{checkpoint.wind_mph.toFixed(0)}</td>
                <td className="r num">{checkpoint.rain_in_hr.toFixed(2)}</td>
                <td className="r num">{checkpoint.snow_in_hr.toFixed(2)}</td>
                <td>
                  <span className="swatch" style={{ background: riskColor(checkpoint.risk_level) }} />
                  {checkpoint.risk}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
