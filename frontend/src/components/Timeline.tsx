import { useCallback, useEffect, useRef, useState } from "react";

import { formatDuration, formatEta } from "../format";
import { RISK_LEVELS } from "../risk";
import type { HeatMetric, Route } from "../types";

const MAX_HOUR = 48;
const PLAY_HOURS_PER_SECOND = 3;
const TICKS = [0, 6, 12, 18, 24, 30, 36, 42, 48];
const METRICS: { value: HeatMetric; label: string }[] = [
  { value: "overall", label: "Overall risk" },
  { value: "wind", label: "Wind" },
  { value: "rain", label: "Rain" },
  { value: "snow", label: "Snow" },
];

export type HeatStatus = "idle" | "loading" | "ready" | "failed";

interface Props {
  departure: string;
  route: Route | null;
  status: HeatStatus;
  metric: HeatMetric;
  onMetricChange(metric: HeatMetric): void;
  /** Fires on every tick; the map handles it without any React render. */
  onHour(hour: number): void;
}

export function Timeline({ departure, route, status, metric, onMetricChange, onHour }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const hourLabelRef = useRef<HTMLSpanElement>(null);
  const clockLabelRef = useRef<HTMLSpanElement>(null);
  const hourRef = useRef(0);
  const [playing, setPlaying] = useState(false);

  const departureMs = Date.parse(departure);

  const showHour = useCallback(
    (hour: number) => {
      hourRef.current = hour;
      onHour(hour);
      if (inputRef.current) {
        inputRef.current.value = String(hour);
        inputRef.current.style.setProperty("--fill", `${(hour / MAX_HOUR) * 100}%`);
      }
      const clock = formatEta(departureMs + hour * 3600_000);
      inputRef.current?.setAttribute("aria-valuetext", `${Math.round(hour)} hours after departure, ${clock}`);
      if (hourLabelRef.current) hourLabelRef.current.textContent = `+${Math.round(hour)}h`;
      if (clockLabelRef.current) clockLabelRef.current.textContent = clock;
    },
    [departureMs, onHour],
  );

  useEffect(() => {
    setPlaying(false);
    showHour(0);
  }, [showHour]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const next = Math.min(hourRef.current + ((now - last) / 1000) * PLAY_HOURS_PER_SECOND, MAX_HOUR);
      last = now;
      showHour(next);
      if (next >= MAX_HOUR) {
        setPlaying(false);
        return;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, showHour]);

  function togglePlay() {
    if (!playing && hourRef.current >= MAX_HOUR) showHour(0);
    setPlaying(!playing);
  }

  const arrivalHour = route ? route.duration_s / 3600 : null;
  const disabled = status !== "ready";

  return (
    <section className="timeline" aria-label="Forecast timeline">
      <div className="timeline-top">
        <button
          type="button"
          className="play"
          disabled={disabled}
          aria-label={playing ? "Pause forecast" : "Play forecast"}
          onClick={togglePlay}
        >
          {playing ? (
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path fill="currentColor" d="M3 2h4v12H3zM9 2h4v12H9z" />
            </svg>
          ) : (
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path fill="currentColor" d="M4 2l10 6-10 6z" />
            </svg>
          )}
        </button>
        <div className="timeline-now" aria-live="off">
          <span ref={hourLabelRef} className="num timeline-hour">+0h</span>
          <span ref={clockLabelRef} className="num timeline-clock">{formatEta(departureMs)}</span>
        </div>
        <div className="segmented segmented-small" role="group" aria-label="Heatmap layer">
          {METRICS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={option.value === metric ? "is-on" : undefined}
              aria-pressed={option.value === metric}
              onClick={() => onMetricChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <ul className="legend" aria-label="Risk legend">
          {RISK_LEVELS.map((level) => (
            <li key={level.key}>
              <span className="swatch" style={{ background: level.color }} />
              {level.label}
            </li>
          ))}
        </ul>
      </div>

      <div className="slider-wrap">
        <input
          ref={inputRef}
          type="range"
          className="slider"
          aria-label="Hours after departure"
          min={0}
          max={MAX_HOUR}
          step={0.1}
          defaultValue={0}
          disabled={disabled}
          onInput={(event) => {
            setPlaying(false);
            showHour(Number(event.currentTarget.value));
          }}
        />
        {arrivalHour !== null && arrivalHour <= MAX_HOUR && (
          <span
            className="arrival-tick"
            style={{ left: `${(arrivalHour / MAX_HOUR) * 100}%` }}
            title={`Route ${route?.name} arrives after ${formatDuration(route!.duration_s)}`}
          >
            <span>Arrive</span>
          </span>
        )}
      </div>
      <div className="ticks" aria-hidden="true">
        {TICKS.map((tick) => (
          <span key={tick} style={{ left: `${(tick / MAX_HOUR) * 100}%` }}>
            {tick}h
          </span>
        ))}
      </div>

      {status === "loading" && <p className="timeline-status">Loading 48-hour forecast for the corridor…</p>}
      {status === "failed" && (
        <p className="timeline-status is-error">Heatmap unavailable: the forecast service is busy. Routes above are unaffected.</p>
      )}
    </section>
  );
}
