import { useState } from "react";
import type {
  Factor,
  FundingMatch,
  Need,
  Opportunity,
  ProjectDocument,
  ScoutRun,
  SiteCandidate,
} from "../types";
import { EstimateRow } from "./EstimateRow";
import { FundingBadge } from "./FundingBadge";

const DOCUMENT_KIND_LABEL: Record<ProjectDocument["kind"], string> = {
  grant_application: "Grant application",
  program_guide: "Program guide",
  tax_form: "Tax form",
  property_agreement: "Property form",
  environmental_review: "Environmental review",
  interconnection: "Interconnection",
  other: "Document",
};

/** Site Scout for this opportunity: present only in the counties with building data. */
export interface ScoutHooks {
  run: ScoutRun | null;
  onStart: () => void;
  onShowCandidate: (candidate: SiteCandidate) => void;
}

function DocumentCard({ doc }: { doc: ProjectDocument }) {
  return (
    <li className="document-card">
      <span className="document-kind">{DOCUMENT_KIND_LABEL[doc.kind]}</span>
      {doc.source_url ? (
        <a href={doc.source_url} target="_blank" rel="noreferrer">
          {doc.name}
        </a>
      ) : (
        <span className="document-name">{doc.name}</span>
      )}
      <p className="muted document-desc">{doc.description}</p>
      {(doc.agency || doc.verified_on) && (
        <p className="muted document-meta">
          {[doc.agency, doc.verified_on ? `Checked ${doc.verified_on}` : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
    </li>
  );
}

/** Funding programs and the forms/packets that go with them, in one place. */
function FundingAndDocuments({
  funding,
  documents,
  checklist,
  siteLabel,
  typed,
  byGemini,
}: {
  funding: FundingMatch[];
  documents: ProjectDocument[];
  checklist: string[];
  siteLabel: string | null;
  typed: boolean;
  byGemini: boolean;
}) {
  if (!typed) {
    return (
      <p className="muted">
        No size estimate or funding lead: this is {byGemini ? "Gemini's own idea, " : ""}not one of
        the project types we have formulas and a funding table for.
      </p>
    );
  }

  const empty = funding.length === 0 && documents.length === 0 && checklist.length === 0;
  return (
    <section className="funding-list">
      <h4>Funding and documents{siteLabel ? ` for ${siteLabel}` : ""}</h4>
      {empty ? (
        <p className="muted">
          No program in our funding table or on Grants.gov matches this yet, and no application
          packets are listed for this project type.
        </p>
      ) : (
        <>
          {funding.map((lead) => (
            <FundingBadge key={lead.program_id} funding={lead} />
          ))}
          {documents.length > 0 && (
            <ul className="document-cards">
              {documents.map((doc) => (
                <DocumentCard key={doc.id} doc={doc} />
              ))}
            </ul>
          )}
          {checklist.length > 0 && (
            <div className="documents-checklist">
              <h5>Also gather locally</h5>
              <ul>
                {checklist.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
              <p className="muted compliance-note">
                Checklist names only — no invented form links. Use your attorney, the owner, or the
                agency for the actual packet.
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ActionPlan({
  title,
  steps,
  compliance,
}: {
  title: string;
  steps: string[];
  compliance: string[];
}) {
  if (steps.length === 0 && compliance.length === 0) return null;
  return (
    <section className="site-action">
      <h4>{title}</h4>
      {steps.length > 0 && (
        <div className="first-steps">
          <h5>Course of action</h5>
          <ol>
            {steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      )}
      {compliance.length > 0 && (
        <div className="compliance-checks">
          <h5>Compliance checks</h5>
          <ul>
            {compliance.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="muted compliance-note">
            Planning checks from known site facts — not legal advice. Confirm permits and local
            rules with counsel and the right agencies.
          </p>
        </div>
      )}
    </section>
  );
}

function collapsedMeta(fundingCount: number, estimateCount: number): string {
  const parts: string[] = [];
  if (fundingCount > 0) parts.push(`${fundingCount} funding lead${fundingCount === 1 ? "" : "s"}`);
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
  const byGemini = opportunity.generated_by === "gemini";
  const typed = opportunity.solution_id !== null;
  const plan = opportunity.plan.length > 0 ? opportunity.plan : opportunity.first_steps;
  const [expanded, setExpanded] = useState(false);
  const documents = opportunity.documents ?? [];
  const documentsNeeded = opportunity.documents_needed ?? [];
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
          {collapsedMeta(opportunity.funding.length, opportunity.estimates.length)}
          {" · "}
          <button type="button" className="linkish" onClick={toggleExpanded}>
            Show plan and funding
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

          <ActionPlan
            title="Plan"
            steps={plan}
            compliance={opportunity.compliance}
          />

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

          <FundingAndDocuments
            funding={opportunity.funding}
            documents={documents}
            checklist={documentsNeeded}
            siteLabel={null}
            typed={typed}
            byGemini={byGemini}
          />

          <button type="button" className="linkish opportunity-collapse" onClick={toggleExpanded}>
            Hide plan and funding
          </button>
        </div>
      )}

    </article>
  );
}
