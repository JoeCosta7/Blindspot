import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { analyze, fetchCounties, FIXTURE_MODE } from "./api";
import { CountySearch } from "./components/CountySearch";
import { type Basemap, MapView, type SelectMode, type Theme } from "./components/MapView";
import { LoadingPanel, ResultsPanel } from "./components/ResultsPanel";
import { featureBounds, unionBounds } from "./format";
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  Asset,
  Bounds,
  CountyCollection,
  CountyFeature,
  LonLat,
} from "./types";

// View preferences are remembered per browser. Storage can be unavailable
// (private windows, blocked site data), so every access is guarded.
function readPref<T extends string>(key: string, allowed: readonly T[]): T | null {
  try {
    const value = localStorage.getItem(key);
    return allowed.includes(value as T) ? (value as T) : null;
  } catch {
    return null;
  }
}

function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // not remembered; the toggle still works for this visit
  }
}

function initialTheme(): Theme {
  const saved = readPref<Theme>("theme", ["light", "dark"]);
  if (saved) return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function ThemeIcon({ theme }: { theme: Theme }) {
  // Shows what the button switches to: a moon in light mode, a sun in dark mode.
  return theme === "light" ? (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </g>
    </svg>
  );
}

interface PendingRequest {
  body: AnalyzeRequest;
  label: string;
}

function Intro({ countiesError }: { countiesError: string | null }) {
  return (
    <div className="panel-body intro">
      <h2>Find what your county can build, and how to pay for it</h2>
      <p>
        Pick a county to see its biggest sustainability gaps, the local sites that could host a
        fix, and funding programs worth checking.
      </p>
      <ol>
        <li>Click a county on the map, or search for it by name.</li>
        <li>
          To compare neighbors, choose <strong>Select area</strong> and drag a box over up to four
          counties.
        </li>
        <li>Open an opportunity to see its estimates, their assumptions, and funding leads.</li>
      </ol>
      {countiesError && (
        <div className="notice notice-warning" role="alert">
          <strong>County boundaries did not load.</strong> {countiesError} Check that the backend is
          running on port 8000.
        </div>
      )}
    </div>
  );
}

export function App() {
  const [counties, setCounties] = useState<CountyCollection | null>(null);
  const [countiesError, setCountiesError] = useState<string | null>(null);
  const [mode, setMode] = useState<SelectMode>("county");
  const [selectedFips, setSelectedFips] = useState<string[]>([]);
  const [fitTo, setFitTo] = useState<Bounds | null>(null);
  const [pending, setPending] = useState<PendingRequest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<AnalyzeResponse | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [focusAsset, setFocusAsset] = useState<Asset | null>(null);
  const inFlight = useRef<AbortController | null>(null);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [basemap, setBasemap] = useState<Basemap>(
    () => readPref<Basemap>("basemap", ["streets", "satellite"]) ?? "streets",
  );

  // Before paint, so the page never flashes the other theme.
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    writePref("theme", next);
  };

  const changeBasemap = (next: Basemap) => {
    setBasemap(next);
    writePref("basemap", next);
  };

  useEffect(() => {
    fetchCounties()
      .then(setCounties)
      .catch((err: unknown) =>
        setCountiesError(err instanceof Error ? err.message : "Unknown error."),
      );
  }, []);

  const featuresByFips = useMemo(
    () => new Map((counties?.features ?? []).map((f) => [f.properties.GEOID, f])),
    [counties],
  );

  const boundsOf = useCallback(
    (fips: string[]): Bounds | null =>
      unionBounds(
        fips
          .map((id) => featuresByFips.get(id))
          .filter((f): f is CountyFeature => f !== undefined)
          .map(featureBounds),
      ),
    [featuresByFips],
  );

  const run = useCallback(
    async (request: PendingRequest) => {
      inFlight.current?.abort(); // a newer selection replaces an older one
      const controller = new AbortController();
      inFlight.current = controller;
      setPending(request);
      setLoading(true);
      setError(null);
      setResponse(null);
      setFocusAsset(null);
      setActiveIndex(0);
      try {
        const result = await analyze(request.body, controller.signal);
        if (controller.signal.aborted) return;
        const fips = result.counties.map((county) => county.fips);
        setResponse(result);
        setSelectedFips(fips);
        if (request.body.mode === "bbox") setFitTo(boundsOf(fips));
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "The request failed.");
      } finally {
        if (inFlight.current === controller) setLoading(false);
      }
    },
    [boundsOf],
  );

  const selectCounty = useCallback(
    (fips: string) => {
      const feature = featuresByFips.get(fips);
      setSelectedFips([fips]);
      setFitTo(boundsOf([fips]));
      void run({
        body: { mode: "county", fips },
        label: feature ? `${feature.properties.NAMELSAD}, ${feature.properties.STUSPS}` : fips,
      });
    },
    [featuresByFips, boundsOf, run],
  );

  const selectBox = useCallback(
    (sw: LonLat, ne: LonLat) => {
      setMode("county"); // one box per click of the button; panning comes back
      setSelectedFips([]);
      void run({ body: { mode: "bbox", sw, ne }, label: "Selected area" });
    },
    [run],
  );

  const switchCounty = (index: number) => {
    const county = response?.counties[index];
    if (!county) return;
    setActiveIndex(index);
    setFocusAsset(null);
    setFitTo(boundsOf([county.fips]));
  };

  const activeReport = response?.counties[activeIndex];
  const assets = useMemo(() => activeReport?.assets ?? [], [activeReport]);

  return (
    <div className="app">
      <header className="toolbar">
        <h1>County Sustainability Planner</h1>
        <CountySearch
          counties={counties}
          onSelect={(county) => selectCounty(county.properties.GEOID)}
        />
        <div className="mode-toggle" role="group" aria-label="Selection mode">
          <button
            type="button"
            className={mode === "county" ? "active" : undefined}
            aria-pressed={mode === "county"}
            onClick={() => setMode("county")}
          >
            Pick a county
          </button>
          <button
            type="button"
            className={mode === "box" ? "active" : undefined}
            aria-pressed={mode === "box"}
            onClick={() => setMode("box")}
          >
            Select area
          </button>
        </div>
        {FIXTURE_MODE && (
          <span className="tag tag-fixture" title="Reports come from saved files, not live data">
            Saved demo data
          </span>
        )}
        <button
          type="button"
          className="theme-toggle"
          aria-pressed={theme === "dark"}
          onClick={toggleTheme}
          title={theme === "light" ? "Switch to dark mode" : "Switch to light mode"}
        >
          <ThemeIcon theme={theme} />
          {theme === "light" ? "Dark mode" : "Light mode"}
        </button>
      </header>

      <main className="layout">
        <MapView
          counties={counties}
          mode={mode}
          basemap={basemap}
          theme={theme}
          onBasemapChange={changeBasemap}
          selectedFips={selectedFips}
          fitTo={fitTo}
          assets={assets}
          focusAsset={focusAsset}
          onCountyClick={selectCounty}
          onBoxSelect={selectBox}
        />

        <aside className="panel" aria-label="Results">
          {loading && pending && <LoadingPanel label={pending.label} />}
          {!loading && error && pending && (
            <div className="panel-body">
              <div className="notice notice-warning" role="alert">
                <strong>The report for {pending.label} could not be loaded.</strong> {error}
              </div>
              <button type="button" className="button" onClick={() => void run(pending)}>
                Try again
              </button>
            </div>
          )}
          {!loading && !error && response && (
            <ResultsPanel
              response={response}
              activeIndex={activeIndex}
              onSelectCounty={switchCounty}
              // A new object each time, so clicking the same site twice flies again.
              onShowOnMap={(asset) => setFocusAsset({ ...asset })}
            />
          )}
          {!loading && !error && !response && <Intro countiesError={countiesError} />}

          <footer className="sources">
            <strong>Data sources</strong>
            <p>
              U.S. Census Bureau, American Community Survey 2020–2024 · FEMA National Risk Index
              v1.20 · EPA RE-Powering screening dataset (2022) · EPA Landfill Methane Outreach
              Program database (September 2024) · NLR PVWatts and Alternative Fuels Station
              Locator · EIA residential electricity sales data (2024) · EPA eGRID2023 · ORNL
              HydroSource non-powered dam assessment (2025) · USDA Census of Agriculture 2022 ·
              EPA AgSTAR digester database · © OpenStreetMap contributors · County boundaries:
              Census 2025.
            </p>
            <p>
              This product uses the Census Bureau Data API but is not endorsed or certified by the
              Census Bureau. Capacity, energy and household figures are estimates. Funding entries
              are leads to check, not findings of eligibility.
            </p>
          </footer>
        </aside>
      </main>
    </div>
  );
}
