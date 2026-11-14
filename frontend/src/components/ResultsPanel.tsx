import { useState } from "react";
import {
  formatAcres,
  formatDollars,
  formatShare,
  formatWhole,
  sourceName,
} from "../format";
import type { AnalyzeResponse, Asset, CountyReport } from "../types";
import { FactorList } from "./FactorList";
import { NeedList } from "./NeedList";
import { OpportunityCard } from "./OpportunityCard";

// The three parts of a report: what is wrong, what the county has, and the
// opportunities Gemini builds from the two.
type Tab = "opportunities" | "needs" | "assets";

const LOADING_SOURCES = [
  "Census ACS: county and national figures",
  "FEMA National Risk Index",
  "CDC Social Vulnerability Index",
  "EPA RE-Powering sites",
  "EPA landfill database",
  "EIA and EPA eGRID: state electricity bills and grid carbon",
  "ORNL: dams without hydropower",
  "USDA farm census and EPA digester database",
  "Geoapify: schools, libraries, colleges",
  "NLR: EV charging stations",
  "NLR PVWatts: solar yield",
  "Open-Meteo: ten years of weather",
  "Gemini: opportunities and brief, written from the needs and assets",
];

export function LoadingPanel({ label }: { label: string }) {
  return (
    <div className="panel-body" aria-busy="true">
      <h2>{label}</h2>
      <p className="muted">Fetching, up to 15 seconds per source:</p>
      <ul className="loading-sources">
        {LOADING_SOURCES.map((source) => (
          <li key={source}>
            <span className="spinner" aria-hidden="true" />
            {source}
          </li>
        ))}
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
  onShowOnMap,
}: {
  report: CountyReport;
  onShowOnMap: (asset: Asset) => void;
}) {
  const assetsById = new Map(report.assets.map((asset) => [asset.id, asset]));
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
          assets={opportunity.asset_ids
            .map((id) => assetsById.get(id))
            .filter((asset): asset is Asset => asset !== undefined)}
          needs={report.needs}
          factors={report.factors}
          note={undefined}
          onShowOnMap={onShowOnMap}
        />
      ))}
    </>
  );
}

function CountyView({
  report,
  onShowOnMap,
}: {
  report: CountyReport;
  onShowOnMap: (asset: Asset) => void;
}) {
  const [tab, setTab] = useState<Tab>("opportunities");
  const tabs: [Tab, string][] = [
    ["opportunities", `Opportunities (${report.opportunities.length})`],
    ["needs", `Needs (${report.needs.length})`],
    ["assets", `Assets (${report.factors.length})`],
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
          <OpportunityList report={report} onShowOnMap={onShowOnMap} />
        )}
        {tab === "needs" && <NeedList needs={report.needs} />}
        {tab === "assets" && (
          <FactorList factors={report.factors} missingAssetTypes={report.missing_asset_types} />
        )}
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
  onShowOnMap: (asset: Asset) => void;
}

export function ResultsPanel({ response, activeIndex, onSelectCounty, onShowOnMap }: Props) {
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
      {report && <CountyView key={report.fips} report={report} onShowOnMap={onShowOnMap} />}
    </div>
  );
}
