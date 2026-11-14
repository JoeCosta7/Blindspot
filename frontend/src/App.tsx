import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { analyze, fetchCounties, fetchNeedMap, FIXTURE_MODE, scout } from "./api";
import { CountySearch } from "./components/CountySearch";
import type { LoadingArea } from "./components/loadingSweep";
import { type Basemap, MapView, type SelectMode, type Theme } from "./components/MapView";
import { PhotoViewer } from "./components/PhotoViewer";
import { LoadingPanel, ResultsPanel, scoutKey } from "./components/ResultsPanel";
import { bestCandidates, bestSites } from "./components/ScoutPanel";
import { featureBounds, unionBounds } from "./format";
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  Asset,
  Bounds,
  CountyCollection,
  CountyFeature,
  LonLat,
  NeedId,
  NeedMapResponse,
  Opportunity,
  ScoutRun,
  SiteCandidate,
} from "./types";

/** A scouted building as a map marker. */
function candidateAsset(candidate: SiteCandidate): Asset {
  return {
    id: candidate.building_id,
    type: "commercial_building",
    name: candidate.name,
    lat: candidate.lat,
    lon: candidate.lon,
    attributes: { status: candidate.status, score: candidate.score },
    source: "Site Scout",
  };
}

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
  /** Covered by the loading animation on the map. */
  area: LoadingArea | null;
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
  const [flare, setFlare] = useState<{ area: LoadingArea } | null>(null);
  const [needMap, setNeedMap] = useState<NeedMapResponse | null>(null);
  const [needMapError, setNeedMapError] = useState<string | null>(null);
  // The need the map is colored by, if any.
  const [needId, setNeedId] = useState<NeedId | null>(null);
  const inFlight = useRef<AbortController | null>(null);
  // One Site Scout run per opportunity, keyed by county and opportunity id.
  const [scoutRuns, setScoutRuns] = useState<Record<string, ScoutRun>>({});
  const scoutInFlight = useRef<Map<string, AbortController>>(new Map());
  // The scouted site whose aerial photo is open over the map.
  const [viewed, setViewed] = useState<SiteCandidate | null>(null);
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
    // Figures for coloring the map by a need. The map works without them.
    fetchNeedMap()
      .then(setNeedMap)
      .catch((err: unknown) => setNeedMapError(err instanceof Error ? err.message : "Unknown error."));
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

  /** The returned counties as one shape, for the finish flare. */
  const areaOf = useCallback(
    (fips: string[]): LoadingArea | null => {
      const polygons: GeoJSON.Position[][][] = [];
      for (const id of fips) {
        const geometry = featuresByFips.get(id)?.geometry;
        if (geometry?.type === "Polygon") polygons.push(geometry.coordinates);
        else if (geometry?.type === "MultiPolygon") polygons.push(...geometry.coordinates);
      }
      return polygons.length > 0 ? { type: "MultiPolygon", coordinates: polygons } : null;
    },
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
      for (const controller of scoutInFlight.current.values()) controller.abort();
      scoutInFlight.current.clear();
      setScoutRuns({});
      setViewed(null);
      try {
        const result = await analyze(request.body, controller.signal);
        if (controller.signal.aborted) return;
        const fips = result.counties.map((county) => county.fips);
        setResponse(result);
        setSelectedFips(fips);
        const done = areaOf(fips);
        if (done) setFlare({ area: done });
        if (request.body.mode === "bbox") setFitTo(boundsOf(fips));
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "The request failed.");
      } finally {
        if (inFlight.current === controller) setLoading(false);
      }
    },
    [boundsOf, areaOf],
  );

  const selectCounty = useCallback(
    (fips: string) => {
      const feature = featuresByFips.get(fips);
      setSelectedFips([fips]);
      setFitTo(boundsOf([fips]));
      void run({
        body: { mode: "county", fips },
        label: feature ? `${feature.properties.NAMELSAD}, ${feature.properties.STUSPS}` : fips,
        area: feature?.geometry ?? null,
      });
    },
    [featuresByFips, boundsOf, run],
  );

  const selectBox = useCallback(
    (sw: LonLat, ne: LonLat) => {
      setMode("county"); // one box per click of the button; panning comes back
      setSelectedFips([]);
      const [w, s] = sw;
      const [e, n] = ne;
      const box: LoadingArea = {
        type: "Polygon",
        coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
      };
      void run({ body: { mode: "bbox", sw, ne }, label: "Selected area", area: box });
    },
    [run],
  );

  /** Start Site Scout for one opportunity and follow its stream. The map
   * follows each inspection; candidates become markers as they are evaluated. */
  const startScout = useCallback((fips: string, opportunity: Opportunity) => {
    const key = scoutKey(fips, opportunity.id);
    scoutInFlight.current.get(key)?.abort();
    const controller = new AbortController();
    scoutInFlight.current.set(key, controller);
    const update = (change: (run: ScoutRun) => ScoutRun) =>
      setScoutRuns((runs) => (runs[key] ? { ...runs, [key]: change(runs[key]) } : runs));
    setScoutRuns((runs) => ({
      ...runs,
      [key]: {
        fips,
        opportunityId: opportunity.id,
        title: opportunity.title,
        running: true,
        steps: [],
        candidates: [],
        result: null,
        error: null,
      },
    }));
    scout(
      fips,
      opportunity.need_ids,
      opportunity.title,
      opportunity.solution_id,
      opportunity.asset_ids,
      (event) => {
        if (controller.signal.aborted) return;
        update((run) => {
          if (event.type === "step" && event.step) {
            return { ...run, steps: [...run.steps, event.step] };
          }
          if (event.type === "candidate" && event.candidate) {
            const id = event.candidate.building_id;
            const others = run.candidates.filter((c) => c.building_id !== id);
            return { ...run, candidates: [...others, event.candidate] };
          }
          if (event.type === "result" && event.result) {
            return {
              ...run,
              running: false,
              result: event.result,
              candidates: event.result.candidates,
            };
          }
          return run;
        });
        // The map holds still while the agent works, then frames the final
        // three sites once. Clicking a site afterwards flies to it.
        if (event.type === "result" && event.result) {
          const d = 0.004;
          const box = unionBounds(
            bestCandidates(event.result.candidates).map(
              (c): Bounds => [c.lon - d, c.lat - d, c.lon + d, c.lat + d],
            ),
          );
          if (box) setFitTo(box);
        }
      },
      controller.signal,
    )
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : "The request failed.";
        update((run) => ({ ...run, running: false, error: message }));
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        update((run) => ({ ...run, running: false }));
      });
  }, []);

  const switchCounty = (index: number) => {
    const county = response?.counties[index];
    if (!county) return;
    setActiveIndex(index);
    setFocusAsset(null);
    setFitTo(boundsOf([county.fips]));
  };

  const activeReport = response?.counties[activeIndex];
  // Only the best scouted sites get markers; the report's own opportunities
  // are county-wide initiatives and draw nothing. Clicking a marker opens the
  // site's photo, so each marker remembers its candidate.
  const scouted = useMemo(() => {
    const byId = new Map<string, SiteCandidate>();
    for (const run of Object.values(scoutRuns)) {
      if (!activeReport || run.fips !== activeReport.fips) continue;
      for (const c of bestSites(run)) byId.set(c.building_id, c);
    }
    return byId;
  }, [activeReport, scoutRuns]);
  const assets = useMemo(() => [...scouted.values()].map(candidateAsset), [scouted]);

  const showCandidate = useCallback((candidate: SiteCandidate) => {
    setFocusAsset(candidateAsset(candidate));
    setViewed(candidate);
  }, []);

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
          loadingArea={loading ? (pending?.area ?? null) : null}
          flare={flare}
          needMap={needMap}
          needMapError={needMapError}
          needId={needId}
          onNeedChange={setNeedId}
          onCountyClick={selectCounty}
          onBoxSelect={selectBox}
          onAssetClick={(asset) => {
            const candidate = scouted.get(asset.id);
            if (candidate) showCandidate(candidate);
          }}
          overlay={
            viewed ? (
              <PhotoViewer
                key={viewed.building_id}
                candidate={viewed}
                onClose={() => setViewed(null)}
              />
            ) : null
          }
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
              onShowOnMap={(asset) => {
                setViewed(null);
                setFocusAsset({ ...asset });
              }}
              onAnalyzeCounty={selectCounty}
              scout={{
                scoutRuns,
                onScout: startScout,
                onShowCandidate: showCandidate,
              }}
            />
          )}
          {!loading && !error && !response && <Intro countiesError={countiesError} />}

          <footer className="sources">
            <strong>Data sources</strong>
            <p>
              U.S. Census Bureau, American Community Survey 2020–2024 · FEMA National Risk Index
              v1.20 · CDC/ATSDR Social Vulnerability Index 2022 · EPA RE-Powering screening dataset (2022) · EPA Landfill Methane Outreach
              Program database (September 2024) · NLR PVWatts and Alternative Fuels Station
              Locator · EIA residential electricity sales data (2024) · EPA eGRID2023 · ORNL
              HydroSource non-powered dam assessment (2025) · USDA Census of Agriculture 2022 ·
              EPA AgSTAR digester database · ESA WorldCover 2021 · NASA SRTM · DOE LEAD Tool
              (2022) · Treasury energy community data (IRS Notice 2026-39) · EIA-860 and EIA-861
              (2025) · USDA ERS Rural-Urban Continuum Codes 2023 · CDC PLACES 2025 · EPA air
              quality design values 2023–2025 · USFS Wildfire Risk to Communities · OpenFEMA
              disaster declarations · EPA ECHO drinking water data · EPA ATTAINS · US Wind Turbine
              Database (USGS) · BTS National Transit Map · Public buildings: Geoapify (data © OpenStreetMap contributors) ·{" "}
              <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">
                Weather data by Open-Meteo.com
              </a>{" "}
              (CC BY 4.0) · County boundaries: Census 2025 · Live federal listings: Grants.gov
              (this product uses the Grants.gov API but is not endorsed or certified by HHS) · Site
              Scout: aerial imagery and property data by Vexcel Imaging; 3D scans by Matterport;
              commercial property records by CoStar Group.
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
