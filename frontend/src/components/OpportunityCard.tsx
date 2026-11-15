import { useState } from "react";
import type {
  Factor,
  Need,
  Opportunity,
  ScoutRun,
  SiteCandidate,
} from "../types";
import { EstimateRow } from "./EstimateRow";

/** Site Scout for this opportunity: present only in the counties with building data. */
export interface ScoutHooks {
  run: ScoutRun | null;
  onStart: () => void;
  onShowCandidate: (candidate: SiteCandidate) => void;
}

/** Funding programs and the forms/packets that go with them, in one place. */
function collapsedMeta(estimateCount: number): string {
  const parts: string[] = [];
  if (estimateCount > 0) parts.push(`${estimateCount} estimate${estimateCount === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

interface Props {
  rank: number;
  opportunity: Opportunity;
  needs: Need[];
  factors: Factor[];
  /** A template note. Gemini opportunities carry their own text instead. */
  note: string | undefined;
  /** Open this opportunity in the workspace. */
  onPursue?: () => void;
}

/** One opportunity: collapsed by default; expand for plan, sites, funding and
 * estimates. */
export function OpportunityCard({
  rank,
  opportunity,
  needs,
  factors,
  note,
  onPursue,
}: Props) {
  const addressed = needs.filter((need) => opportunity.need_ids.includes(need.id));
  // Absent in reports saved before the field existed.
  const fits = new Map((opportunity.need_fits ?? []).map((fit) => [fit.need_id, fit.text]));
  const drawsOn = factors.filter((factor) => opportunity.factor_ids.includes(factor.id));
  const typed = opportunity.solution_id !== null;
  const [expanded, setExpanded] = useState(false);
  const detailId = `opportunity-detail-${opportunity.id}`;

  function toggleExpanded() {
    setExpanded((open) => !open);
  }

  return (
    <article
      className={
        expanded
          ? "card opportunity opportunity-primary"
          : "card opportunity opportunity-primary opportunity-collapsed"
      }
    >
      <header className="opportunity-head">
        <span className="rank">{rank}</span>
        <h3>
          <button
            type="button"
            className="opportunity-toggle"
            onClick={toggleExpanded}
            aria-expanded={expanded}
            aria-controls={detailId}
          >
            <span className="opportunity-toggle-label">{opportunity.title}</span>
            <span className="opportunity-chevron" aria-hidden="true">
              {expanded ? "▾" : "▸"}
            </span>
          </button>
        </h3>
        <span
          className="score"
          title="Set by code, not by Gemini: halfway between the highest and the mean priority of the needs addressed, reduced when the site lacks the data an estimate needs"
        >
          Score {opportunity.score}
        </span>
        {onPursue && (
          <button type="button" className="button pursue-button" onClick={onPursue}>
            Pursue this
          </button>
        )}
      </header>

      {opportunity.description && (
        <p className="opportunity-description">{opportunity.description}</p>
      )}
      {opportunity.summary && <p className="note">{opportunity.summary}</p>}

      {addressed.length > 0 && fits.size === 0 && (
        <div className="chips">
          <span className="chips-label">Addresses</span>
          {addressed.map((need) => (
            <span key={need.id} className={`chip chip-${need.level}`}>
              {need.label}
            </span>
          ))}
        </div>
      )}
      {addressed.length > 0 && fits.size > 0 && (
        // Each need with how the project reduces it, so the tie to the
        // county's problems can be read without opening the card.
        <div className="need-fits">
          <span className="chips-label">Addresses</span>
          <ul>
            {addressed.map((need) => (
              <li key={need.id}>
                <span className={`chip chip-${need.level}`}>{need.label}</span>
                {fits.get(need.id) && <span className="need-fit-text">{fits.get(need.id)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!expanded && (
        <p className="muted opportunity-preview">
          {collapsedMeta(opportunity.estimates.length)}
          {" · "}
          <button type="button" className="linkish" onClick={toggleExpanded}>
            Show details
          </button>
        </p>
      )}

      {expanded && (
        <div id={detailId} className="opportunity-detail">
          {opportunity.rationale && (
            <p className="note">
              <strong>Why here:</strong> {opportunity.rationale}
            </p>
          )}
          {note && <p className="note">{note}</p>}

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

          <button type="button" className="linkish opportunity-collapse" onClick={toggleExpanded}>
            Hide details
          </button>
        </div>
      )}

    </article>
  );
}
