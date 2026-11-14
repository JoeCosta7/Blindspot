import { useMemo, useState } from "react";
import type { CountyCollection, CountyFeature } from "../types";

const MAX_RESULTS = 8;

interface Props {
  counties: CountyCollection | null;
  onSelect: (county: CountyFeature) => void;
}

const normalize = (text: string) =>
  text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "") // "Doña Ana" matches "dona ana"
    .replace(/[^a-z0-9 ]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

export function CountySearch({ counties, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  // "broome county ny" for each county, built once.
  const index = useMemo(
    () =>
      (counties?.features ?? []).map((feature) => ({
        feature,
        key: normalize(`${feature.properties.NAMELSAD} ${feature.properties.STUSPS}`),
      })),
    [counties],
  );

  const results = useMemo(() => {
    const words = normalize(query).split(" ").filter(Boolean);
    if (words.length === 0) return [];
    const starts: CountyFeature[] = [];
    const contains: CountyFeature[] = [];
    for (const { feature, key } of index) {
      if (!words.every((word) => key.includes(word))) continue;
      (key.startsWith(words[0] ?? "") ? starts : contains).push(feature);
      if (starts.length >= MAX_RESULTS) break;
    }
    return [...starts, ...contains].slice(0, MAX_RESULTS);
  }, [index, query]);

  const choose = (county: CountyFeature) => {
    setQuery(`${county.properties.NAMELSAD}, ${county.properties.STUSPS}`);
    setOpen(false);
    onSelect(county);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => Math.min(i + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      const county = results[active];
      if (county) choose(county);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  const showList = open && query.trim().length > 0;

  return (
    <div className="search">
      <input
        type="search"
        role="combobox"
        aria-label="Search for a county"
        aria-expanded={showList}
        aria-controls="county-results"
        aria-autocomplete="list"
        placeholder={counties ? "Search for a county, e.g. Mercer WV" : "Loading counties…"}
        disabled={!counties}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {showList && (
        <ul id="county-results" className="search-results" role="listbox">
          {results.length === 0 && <li className="search-empty">No county matches.</li>}
          {results.map((county, i) => (
            <li
              key={county.properties.GEOID}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : undefined}
              // mousedown, not click: it fires before the input's blur closes the list
              onMouseDown={(event) => {
                event.preventDefault();
                choose(county);
              }}
              onMouseEnter={() => setActive(i)}
            >
              {county.properties.NAMELSAD}, {county.properties.STUSPS}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
