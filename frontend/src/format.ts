const clock = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  hour: "numeric",
  minute: "2-digit",
});

const zone = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" });

export function formatEta(iso: string | number): string {
  return clock.format(new Date(iso));
}

export function timeZoneName(): string {
  const part = zone.formatToParts(new Date()).find((p) => p.type === "timeZoneName");
  return part?.value ?? "local";
}

export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${String(minutes % 60).padStart(2, "0")}m` : `${minutes}m`;
}

export function formatMiles(miles: number): string {
  return Math.round(miles).toLocaleString();
}

/** Value for a datetime-local input, in the browser's zone. */
export function toLocalInput(date: Date): string {
  const offsetMs = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

export function nextHour(): Date {
  const date = new Date();
  date.setHours(date.getHours() + 1, 0, 0, 0);
  return date;
}
