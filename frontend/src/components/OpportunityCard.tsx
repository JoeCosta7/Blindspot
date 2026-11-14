import { ASSET_STYLES, formatNumber } from "../format";
import type { Asset, Estimate, Need, Opportunity } from "../types";
import { FundingBadge } from "./FundingBadge";

function EstimateRow({ estimate }: { estimate: Estimate }) {
  return (
    <li className="estimate">
      <div className="estimate-line">
        <span className="estimate-label">{estimate.label}</span>
        <span className="estimate-value">
          {formatNumber(estimate.value)} {estimate.unit}
        </span>
        {/* The tag is on every estimate; assumptions show on hover and on click. */}
        <span className="tag tag-estimate" title={estimate.assumptions.join("\n")}>
          Estimate
        </span>
      </div>
      <details className="assumptions">
        <summary>Assumptions</summary>
        <ul>
          {estimate.assumptions.map((assumption) => (
            <li key={assumption}>{assumption}</li>
          ))}
        </ul>
      </details>
    </li>
  );
}

interface Props {
  rank: number;
  opportunity: Opportunity;
  asset: Asset | undefined;
  needs: Need[];
  note: string | undefined;
  onShowOnMap: (asset: Asset) => void;
}

export function OpportunityCard({ rank, opportunity, asset, needs, note, onShowOnMap }: Props) {
  const addressed = needs.filter((need) => opportunity.need_ids.includes(need.id));
  return (
    <article className="card opportunity">
      <header className="opportunity-head">
        <span className="rank">{rank}</span>
        <h3>{opportunity.title}</h3>
        <span className="score" title="Mean severity of the needs addressed, reduced when the site lacks the data an estimate needs">
          Score {opportunity.score}
        </span>
      </header>

      {asset && (
        <button type="button" className="link-button" onClick={() => onShowOnMap(asset)}>
          <span className="legend-dot" style={{ background: ASSET_STYLES[asset.type].color }} />
          {ASSET_STYLES[asset.type].label} · Show on map
        </button>
      )}

      {note && <p className="note">{note}</p>}

      {addressed.length > 0 && (
        <div className="chips">
          <span className="chips-label">Addresses</span>
          {addressed.map((need) => (
            <span key={need.id} className={`chip chip-${need.level}`}>
              {need.label}
            </span>
          ))}
        </div>
      )}

      {opportunity.estimates.length > 0 ? (
        <ul className="estimates">
          {opportunity.estimates.map((estimate) => (
            <EstimateRow key={estimate.label} estimate={estimate} />
          ))}
        </ul>
      ) : (
        <p className="muted">No size estimate: the data needed for one is not available.</p>
      )}

      <section className="funding-list">
        <h4>Funding leads</h4>
        {opportunity.funding.length === 0 ? (
          <p className="muted">No program in the curated funding table matches this yet.</p>
        ) : (
          opportunity.funding.map((funding) => (
            <FundingBadge key={funding.program_id} funding={funding} />
          ))
        )}
      </section>
    </article>
  );
}
