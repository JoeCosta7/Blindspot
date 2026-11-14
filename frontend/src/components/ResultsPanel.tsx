import { useState } from "react";
import {
  ASSET_STYLES,
  ASSET_TYPE_ORDER,
  formatAcres,
  formatDollars,
  formatShare,
  formatWhole,
  sourceName,
} from "../format";
import type { AnalyzeResponse, Asset, CountyReport } from "../types";
import { NeedList } from "./NeedList";
import { OpportunityCard } from "./OpportunityCard";

type Tab = "opportunities" | "needs" | "assets";

const LOADING_SOURCES = [
  "Census ACS: county and national figures",
  "FEMA National Risk Index",
  "EPA RE-Powering sites",
  "EPA landfill database",
  "OpenStreetMap: schools, libraries, colleges",
  "NLR: EV charging stations",
  "NLR PVWatts: solar yield",
  "Gemini: written brief",
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
  // The Gemini entry is about how the brief was written, not missing data.
  const failed = report.source_status.filter((s) => !s.ok && s.source !== "gemini");
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

function AssetList({
  report,
  onShowOnMap,
}: {
  report: CountyReport;
  onShowOnMap: (asset: Asset) => void;
}) {
  const types = ASSET_TYPE_ORDER.filter((type) => (report.asset_counts[type] ?? 0) > 0);
  return (
    <div className="asset-list">
      {types.length === 0 && <p className="muted">No sites were found from the data that loaded.</p>}
      {types.map((type) => {
        const shown = report.assets.filter((asset) => asset.type === type);
        const total = report.asset_counts[type] ?? shown.length;
        return (
          <details key={type} className="card asset-group">
            <summary>
              <span className="legend-dot" style={{ background: ASSET_STYLES[type].color }} />
              <span className="asset-group-name">{ASSET_STYLES[type].plural}</span>
              <span className="count">{formatWhole(total)}</span>
            </summary>
            {shown.length < total && (
              <p className="muted">
                Showing {formatWhole(shown.length)} of {formatWhole(total)} on the map and below,
                largest first.
              </p>
            )}
            <ul>
              {shown.map((asset) => (
                <li key={asset.id}>
                  <button type="button" className="link-button" onClick={() => onShowOnMap(asset)}>
                    {asset.name}
                  </button>
                  {typeof asset.attributes.acres === "number" && (
                    <span className="muted"> · {formatAcres(asset.attributes.acres)}</span>
                  )}
                  {asset.attributes.is_candidate === true && (
                    <span className="muted"> · EPA candidate for a gas project</span>
                  )}
                </li>
              ))}
            </ul>
          </details>
        );
      })}
      {report.missing_asset_types.length > 0 && (
        <p className="muted">
          Looked for and not found:{" "}
          {report.missing_asset_types
            .map((type) => ASSET_STYLES[type].plural.toLowerCase())
            .join(", ")}
          .
        </p>
      )}
      <p className="muted">
        EPA's landfill gas database does not list every US landfill. The RE-Powering dataset does
        not record whether a landfill is open or closed.
      </p>
    </div>
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
  const assetsById = new Map(report.assets.map((asset) => [asset.id, asset]));
  // Template notes only restate what the card already shows, so they are
  // displayed only when Gemini wrote them.
  const notes = new Map(
    report.narrative.generated_by === "gemini"
      ? report.narrative.opportunity_notes.map((note) => [note.opportunity_id, note.text])
      : [],
  );
  const totalAssets = Object.values(report.asset_counts).reduce((sum, n) => sum + n, 0);
  const tabs: [Tab, string][] = [
    ["opportunities", `Opportunities (${report.opportunities.length})`],
    ["needs", `Needs (${report.needs.length})`],
    ["assets", `Sites (${formatWhole(totalAssets)})`],
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
        {tab === "opportunities" &&
          (report.opportunities.length === 0 ? (
            <p className="muted">
              No opportunity could be matched. An opportunity needs a flagged need and a local site
              that can host a fix for it. See the Needs and Sites tabs for what was found.
            </p>
          ) : (
            report.opportunities.map((opportunity, i) => (
              <OpportunityCard
                key={opportunity.id}
                rank={i + 1}
                opportunity={opportunity}
                asset={assetsById.get(opportunity.asset_id)}
                needs={report.needs}
                note={notes.get(opportunity.id)}
                onShowOnMap={onShowOnMap}
              />
            ))
          ))}
        {tab === "needs" && <NeedList needs={report.needs} />}
        {tab === "assets" && <AssetList report={report} onShowOnMap={onShowOnMap} />}
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
