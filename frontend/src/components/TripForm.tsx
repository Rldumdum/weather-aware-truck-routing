import { useState } from "react";

import { nextHour, toLocalInput } from "../format";
import type { Place, SampleInterval, TripRequest } from "../types";
import { PlaceInput } from "./PlaceInput";

const SAMPLE_INTERVALS: SampleInterval[] = [10, 25, 50];
const MAX_DEPARTURE_DAYS = 7;

const DEMO_ORIGIN: Place = { label: "Denver, Colorado, United States", lat: 39.7392, lon: -104.9849 };
const DEMO_DESTINATION: Place = { label: "Salt Lake City, Utah, United States", lat: 40.7596, lon: -111.8868 };

interface Props {
  busy: boolean;
  onSubmit(trip: TripRequest): void;
  onIntervalChange(interval: SampleInterval): void;
}

export function TripForm({ busy, onSubmit, onIntervalChange }: Props) {
  const [origin, setOrigin] = useState<Place | null>(DEMO_ORIGIN);
  const [destination, setDestination] = useState<Place | null>(DEMO_DESTINATION);
  const [departure, setDeparture] = useState(() => toLocalInput(nextHour()));
  const [loadLb, setLoadLb] = useState("38000");
  const [interval, setInterval] = useState<SampleInterval>(25);

  const load = Number(loadLb);
  const ready = origin && destination && departure && load > 0;
  const now = new Date();
  const latest = new Date(now.getTime() + MAX_DEPARTURE_DAYS * 24 * 3600 * 1000);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!origin || !destination || !ready) return;
    onSubmit({
      origin,
      destination,
      departure: new Date(departure).toISOString(),
      load_lb: load,
      sample_interval_mi: interval,
    });
  }

  function chooseInterval(next: SampleInterval) {
    setInterval(next);
    onIntervalChange(next);
  }

  return (
    <form className="trip-form" onSubmit={submit}>
      <PlaceInput label="Origin" value={origin} onChange={setOrigin} />
      <PlaceInput label="Destination" value={destination} onChange={setDestination} />
      <div className="field-row">
        <div className="field">
          <label htmlFor="departure">Departure</label>
          <input
            id="departure"
            type="datetime-local"
            value={departure}
            min={toLocalInput(now)}
            max={toLocalInput(latest)}
            onChange={(event) => setDeparture(event.target.value)}
          />
        </div>
        <div className="field load-field">
          <label htmlFor="load">Load weight</label>
          <div className="input-suffix">
            <input
              id="load"
              inputMode="numeric"
              value={loadLb}
              onChange={(event) => setLoadLb(event.target.value.replace(/\D/g, ""))}
            />
            <span>lb</span>
          </div>
        </div>
      </div>
      <fieldset className="field">
        <legend>Sample weather every</legend>
        <div className="segmented">
          {SAMPLE_INTERVALS.map((value) => (
            <label key={value} className={value === interval ? "is-on" : undefined}>
              <input
                type="radio"
                name="interval"
                checked={value === interval}
                onChange={() => chooseInterval(value)}
              />
              {value} mi
            </label>
          ))}
        </div>
      </fieldset>
      <button className="primary" type="submit" disabled={busy || !ready}>
        {busy ? "Checking routes…" : "Compare routes"}
      </button>
    </form>
  );
}
