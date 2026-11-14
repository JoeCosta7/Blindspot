import type { Asset, Factor, Need, Opportunity, ScoutRun, SiteCandidate } from "../types";
import { EstimateRow } from "./EstimateRow";
import { FundingBadge } from "./FundingBadge";
import { ScoutPanel } from "./ScoutPanel";

/** Site Scout for this opportunity: present only in the counties with building data. */
export interface ScoutHooks {
  run: ScoutRun | null;
  onStart: () => void;
  onShowCandidate: (candidate: SiteCandidate) => void;
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
  scout?: ScoutHooks;
}

/** One opportunity: what Gemini wrote (the plan, why here, first steps), the
 * needs it addresses and the factors and sites it draws on, then what code
 * added (score, estimates, funding leads). */
export function OpportunityCard({
  rank,
  opportunity,
  needs,
  factors,
  note,
  scout,
}: Props) {
  const addressed = needs.filter((need) => opportunity.need_ids.includes(need.id));
  const drawsOn = factors.filter((factor) => opportunity.factor_ids.includes(factor.id));
  const byGemini = opportunity.generated_by === "gemini";
  const typed = opportunity.solution_id !== null;

  return (
    <article className="card opportunity opportunity-primary">
      <header className="opportunity-head">
        <span className="rank">{rank}</span>
        {/* The opportunity is general; Site Scout finds the building. */}
        <h3>{opportunity.title}</h3>
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

      {scout && (
        <section className="scout-section">
          <h4>Site Scout</h4>
          <ScoutPanel
            run={scout.run}
            needs={addressed}
            title={opportunity.title}
            onStart={scout.onStart}
            onShowOnMap={scout.onShowCandidate}
          />
        </section>
      )}
    </article>
  );
}
