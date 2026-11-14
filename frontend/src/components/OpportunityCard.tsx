import { ASSET_STYLES, formatNumber } from "../format";
import type { Asset, Estimate, Factor, Need, Opportunity } from "../types";
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

/** Who owns the dam decides whether the county can act on it at all. */
function DamFacts({ asset }: { asset: Asset }) {
  const { owner_type, waterway } = asset.attributes;
  const facts = [
    typeof waterway === "string" ? `On ${waterway}` : null,
    typeof owner_type === "string" ? `Owner type: ${owner_type}` : null,
  ].filter((fact): fact is string => fact !== null);
  if (facts.length === 0) return null;
  return <p className="muted">{facts.join(" · ")}</p>;
}

interface Props {
  rank: number;
  opportunity: Opportunity;
  /** The sites the opportunity names, looked up on the map; the main one first. */
  assets: Asset[];
  needs: Need[];
  factors: Factor[];
  /** A template note. Gemini opportunities carry their own text instead. */
  note: string | undefined;
  onShowOnMap: (asset: Asset) => void;
}

/** One opportunity: what Gemini wrote (the plan, why here, first steps), the
 * needs it addresses and the factors and sites it draws on, then what code
 * added (score, estimates, funding leads). */
export function OpportunityCard({
  rank,
  opportunity,
  assets,
  needs,
  factors,
  note,
  onShowOnMap,
}: Props) {
  const addressed = needs.filter((need) => opportunity.need_ids.includes(need.id));
  const drawsOn = factors.filter((factor) => opportunity.factor_ids.includes(factor.id));
  const main = assets[0];
  const byGemini = opportunity.generated_by === "gemini";
  const typed = opportunity.solution_id !== null;

  return (
    <article className="card opportunity opportunity-primary">
      <header className="opportunity-head">
        <span className="rank">{rank}</span>
        <h3>
          {main ? (
            <button
              type="button"
              className="opportunity-title"
              onClick={() => onShowOnMap(main)}
              title="Show this site on the map"
            >
              {opportunity.title}
            </button>
          ) : (
            opportunity.title
          )}
        </h3>
        <span
          className="score"
          title="Set by code, not by Gemini: halfway between the highest and the mean priority of the needs addressed, reduced when the site lacks the data an estimate needs"
        >
          Score {opportunity.score}
        </span>
      </header>

      {opportunity.summary && <p className="note">{opportunity.summary}</p>}
      {opportunity.rationale && (
        <p className="note">
          <strong>Why here:</strong> {opportunity.rationale}
        </p>
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

      {drawsOn.length > 0 && (
        <div className="chips">
          <span className="chips-label">Draws on</span>
          {drawsOn.map((factor) => (
            <span
              key={factor.id}
              className="chip chip-factor"
              title={[factor.value, factor.comparison].filter(Boolean).join(" · ")}
            >
              {factor.label}
            </span>
          ))}
        </div>
      )}

      {assets.length > 0 ? (
        <ul className="opportunity-sites">
          {assets.map((asset) => (
            <li key={asset.id}>
              <button type="button" className="link-button" onClick={() => onShowOnMap(asset)}>
                <span
                  className="legend-dot"
                  style={{ background: ASSET_STYLES[asset.type].color }}
                />
                {asset.name}
              </button>
              {asset.type === "non_powered_dam" && <DamFacts asset={asset} />}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">
          {opportunity.solution_id === "farm_digester"
            ? "County-wide: the data gives a count of farms, not their locations, so no site is named."
            : "County-wide: no single site is named."}
        </p>
      )}

      {opportunity.first_steps.length > 0 && (
        <section className="first-steps">
          <h4>First steps</h4>
          <ol>
            {opportunity.first_steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </section>
      )}

      {opportunity.estimates.length > 0 && (
        <ul className="estimates">
          {opportunity.estimates.map((estimate) => (
            <EstimateRow key={estimate.label} estimate={estimate} />
          ))}
        </ul>
      )}
      {opportunity.estimates.length === 0 && typed && (
        <p className="muted">No size estimate: the data needed for one is not available.</p>
      )}

      {typed ? (
        <section className="funding-list">
          <h4>Funding leads</h4>
          {opportunity.funding.length === 0 ? (
            <p className="muted">No program in our funding table or on Grants.gov matches this yet.</p>
          ) : (
            opportunity.funding.map((funding) => (
              <FundingBadge key={funding.program_id} funding={funding} />
            ))
          )}
        </section>
      ) : (
        <p className="muted">
          No size estimate or funding lead: this is {byGemini ? "Gemini's own idea, " : ""}not one
          of the project types we have formulas and a funding table for.
        </p>
      )}
    </article>
  );
}
