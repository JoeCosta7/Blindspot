import { useState } from "react";
import {
  formatAcres,
  formatDollars,
  formatShare,
  formatWhole,
  LOADING_SOURCE_IDS,
  sourceName,
} from "../format";
import type {
  AnalyzeResponse,
  CountyReport,
  Opportunity,
  ScoutRun,
  SiteCandidate,
  SourceStatus,
} from "../types";
import { FactorList } from "./FactorList";
import { NeedList } from "./NeedList";
import { OpportunityCard } from "./OpportunityCard";
import { PeerList } from "./PeerList";

// The three parts of a report: what is wrong, what the county has, and the
// opportunities Gemini builds from the two. Site Scout lives on each
// opportunity card, in the counties with building data.
type Tab = "opportunities" | "needs" | "assets" | "peers";

/** Scout runs are keyed by county and opportunity. */
export const scoutKey = (fips: string, opportunityId: string) => `${fips}:${opportunityId}`;

export interface ScoutProps {
  scoutRuns: Record<string, ScoutRun>;
  onScout: (fips: string, opportunity: Opportunity) => void;
  onShowCandidate: (candidate: SiteCandidate) => void;
  /** Open an opportunity in the workspace. */
  onPursue: (fips: string, opportunity: Opportunity) => void;
}

/** Live checklist while `/api/analyze?progress=1` streams each source. */
export function LoadingPanel({
  label,
  progress,
}: {
  label: string;
  progress: Record<string, SourceStatus>;
}) {
  const known = new Set<string>(LOADING_SOURCE_IDS);
  const extra = Object.keys(progress).filter(
    (id) => !known.has(id) && id !== "pvwatts_site",
  );
  const ids = [...LOADING_SOURCE_IDS, ...extra];
  const done = ids.filter((id) => progress[id]).length;
  const failed = ids.filter((id) => progress[id] && !progress[id].ok).length;

  return (
    <div className="panel-body" aria-busy="true">
      <h2>{label}</h2>
      <p className="muted">
        Fetching sources ({done} of {ids.length} done
        {failed > 0 ? `, ${failed} failed` : ""}
        ). Up to 15 seconds each:
      </p>
      <ul className="loading-sources">
        {ids.map((id) => {
          const status = progress[id];
          const state = !status ? "pending" : status.ok ? "ok" : "fail";
          return (
            <li key={id} className={`loading-source loading-source-${state}`}>
              <span className="loading-mark" aria-hidden="true">
                {state === "pending" ? (
                  <span className="spinner" />
                ) : state === "ok" ? (
                  "✓"
                ) : (
                  "✕"
                )}
              </span>
              <span className="loading-source-label">
                {sourceName(id)}
                {status?.cached ? " (cached)" : ""}
                {status && !status.ok && status.message ? ` — ${status.message}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
      <div className="skeleton" />
      <div className="skeleton" />
      <div className="skeleton short" />
    </div>
  );
}

function ContextStats({ report }: { report: CountyReport }) {
  const { context } = report;
  const stats: [string, string | null][] = [
    ["Population", report.population !== null ? formatWhole(report.population) : null],
    [
      "Median household income",
      context.median_household_income !== null
        ? formatDollars(context.median_household_income)
        : null,
    ],
    ["Poverty rate", context.poverty_rate !== null ? formatShare(context.poverty_rate) : null],
    [
      "Unemployment",
      context.unemployment_rate !== null ? formatShare(context.unemployment_rate) : null,
    ],
    [
      "Homes with electric heat",
      context.electric_heat_share !== null ? formatShare(context.electric_heat_share) : null,
    ],
    [
      "Solar yield",
      report.resources.solar_kwh_per_kw_year !== null
        ? `${formatWhole(report.resources.solar_kwh_per_kw_year)} kWh per kW a year`
        : null,
    ],
    [
      "Electricity price (state average)",
      context.state_electricity_price_cents_kwh !== null
        ? `${context.state_electricity_price_cents_kwh.toFixed(1)} cents per kWh`
        : null,
    ],
    [
      "Land in farms",
      report.resources.land_in_farms_acres !== null
        ? formatAcres(report.resources.land_in_farms_acres)
        : null,
    ],
  ];
  const known = stats.filter((stat): stat is [string, string] => stat[1] !== null);
  if (known.length === 0) return null;
  return (
    <dl className="stats">
      {known.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function SourceNotice({ report }: { report: CountyReport }) {
  // The Gemini entry is about who wrote the opportunities, not missing data.
  // The Opportunities tab reports it.
  const failed = report.source_status.filter((s) => !s.ok && !s.source.startsWith("gemini"));
  if (failed.length === 0) return null;
  return (
    <div className="notice notice-warning" role="status">
      <strong>
        {failed.length === 1 ? "One data source" : `${failed.length} data sources`} did not load.
        This report is incomplete.
      </strong>
      <ul>
        {failed.map((status) => (
          <li key={status.source}>
            {sourceName(status.source)}
            {status.message && `: ${status.message}`}
          </li>
        ))}
      </ul>
    </div>
  );
}

function OpportunityList({
  report,
  scout,
}: {
  report: CountyReport;
  scout: ScoutProps;
}) {
  const byGemini = report.opportunities.some((o) => o.generated_by === "gemini");
  const gemini = report.source_status.find((s) => s.source === "gemini");

  if (report.opportunities.length === 0) {
    return (
      <div className="muted">
        <p>
          No opportunity could be written. An opportunity uses something the county has to meet a
          flagged need. See the Needs and Assets tabs for what was found.
        </p>
        {gemini && !gemini.ok && gemini.message && <p>{gemini.message}.</p>}
      </div>
    );
  }
  return (
    <>
      {byGemini ? (
        <p className="muted">
          Written by Gemini from this county's needs and assets. Each one was checked to use only
          needs, sites and numbers from this report. The scores, estimates and funding leads are
          added by code. Feasibility and cost were not checked.
        </p>
      ) : (
        <div className="notice notice-info" role="status">
          Gemini did not write these{gemini?.message ? ` (${gemini.message})` : ""}. Showing the
          rule-based matches instead: each pairs a flagged need with a local site that can host a
          standard fix for it.
        </div>
      )}
      {report.opportunities.map((opportunity, i) => (
        <OpportunityCard
          key={opportunity.id}
          rank={i + 1}
          opportunity={opportunity}
          needs={report.needs}
          factors={report.factors}
          note={undefined}
          onPursue={() => scout.onPursue(report.fips, opportunity)}
        />
      ))}
    </>
  );
}

function CountyView({
  report,
  scout,
  onAnalyzeCounty,
}: {
  report: CountyReport;
  scout: ScoutProps;
  onAnalyzeCounty: (fips: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("opportunities");
  const tabs: [Tab, string][] = [
    ["opportunities", `Opportunities (${report.opportunities.length})`],
    ["needs", `Needs (${report.needs.length})`],
    ["assets", `Assets (${report.factors.length})`],
    ["peers", `Peers (${report.peers.length})`],
  ];

  return (
    <>
      <header className="county-head">
        <h2>
          {report.name}, {report.state}
        </h2>
        <ContextStats report={report} />
      </header>

      <SourceNotice report={report} />

      <section className="brief">
        <p>{report.narrative.summary}</p>
        <p className="brief-byline">
          {report.narrative.generated_by === "gemini"
            ? "Written by Gemini from the figures in this report. Every number was checked against them."
            : "Written from a fixed template, without AI."}
        </p>
      </section>

      <div className="tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "active" : undefined}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {tab === "opportunities" && (
          <OpportunityList report={report} scout={scout} />
        )}
        {tab === "needs" && <NeedList needs={report.needs} />}
        {tab === "assets" && (
          <FactorList
            factors={report.factors}
            highlights={report.highlights ?? []}
            missingAssetTypes={report.missing_asset_types}
          />
        )}
        {tab === "peers" && <PeerList report={report} onAnalyze={onAnalyzeCounty} />}
      </div>

      {report.narrative.caveats.length > 0 && (
        <section className="caveats">
          <h4>Before you act on this</h4>
          <ul>
            {report.narrative.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

interface Props {
  response: AnalyzeResponse;
  activeIndex: number;
  onSelectCounty: (index: number) => void;
  /** Run a fresh report for another county (a peer). */
  onAnalyzeCounty: (fips: string) => void;
  /** Site Scout runs, one per opportunity, and the hooks to start and show them. */
  scout: ScoutProps;
}

export function ResultsPanel({
  response,
  activeIndex,
  onSelectCounty,
  onAnalyzeCounty,
  scout,
}: Props) {
  const report = response.counties[activeIndex];
  return (
    <div className="panel-body">
      {response.message && (
        <div className="notice notice-info" role="status">
          {response.message}
        </div>
      )}
      {response.counties.length > 1 && (
        <div className="county-switcher" role="tablist" aria-label="Selected counties">
          {response.counties.map((county, i) => (
            <button
              key={county.fips}
              type="button"
              role="tab"
              aria-selected={i === activeIndex}
              className={i === activeIndex ? "active" : undefined}
              onClick={() => onSelectCounty(i)}
            >
              {county.name}, {county.state}
            </button>
          ))}
        </div>
      )}
      {/* key: each county gets its own tab state */}
      {report && (
        <CountyView
          key={report.fips}
          report={report}
          scout={scout}
          onAnalyzeCounty={onAnalyzeCounty}
        />
      )}
    </div>
  );
}
