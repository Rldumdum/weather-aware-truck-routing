import { useEffect, useId, useRef, useState } from "react";

import { searchPlaces } from "../api";
import type { Place } from "../types";

const SEARCH_DELAY_MS = 220;

interface Props {
  label: string;
  value: Place | null;
  onChange(place: Place | null): void;
}

export function PlaceInput({ label, value, onChange }: Props) {
  const [text, setText] = useState(value?.label ?? "");
  const [query, setQuery] = useState("");  // only what the user typed, never a chosen label
  const [results, setResults] = useState<Place[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const inputId = useId();

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([]);
      setOpen(false);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      searchPlaces(query, controller.signal)
        .then((places) => {
          setResults(places);
          setActive(0);
          setOpen(document.activeElement === inputRef.current);
          // Typed text with no explicit pick still resolves to the top match.
          onChange(places[0] ?? null);
        })
        .catch(() => {
          // An aborted or failed search leaves the previous suggestions in place.
        });
    }, SEARCH_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  function choose(place: Place) {
    setText(place.label);
    setQuery("");
    onChange(place);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || !results.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((index) => (index + step + results.length) % results.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(results[active]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="field place-field">
      <label htmlFor={inputId}>{label}</label>
      <input
        ref={inputRef}
        id={inputId}
        value={text}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        placeholder="City, address or terminal"
        onChange={(event) => {
          setText(event.target.value);
          setQuery(event.target.value);
          onChange(null);
        }}
        onFocus={(event) => event.target.select()}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {open && results.length > 0 && (
        <ul id={listId} role="listbox" className="place-list">
          {results.map((place, index) => (
            <li
              key={`${place.lat},${place.lon}`}
              role="option"
              aria-selected={index === active}
              className={index === active ? "is-active" : undefined}
              onMouseEnter={() => setActive(index)}
              onMouseDown={(event) => {
                event.preventDefault();
                choose(place);
              }}
            >
              {place.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
