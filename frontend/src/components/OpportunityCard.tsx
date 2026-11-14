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

function collapsedMeta(assets: Asset[], fundingCount: number, estimateCount: number): string {
  const parts: string[] = [];
  const only = assets[0];
  if (assets.length === 1 && only) parts.push(`1 site: ${only.name}`);
  else if (assets.length > 1) parts.push(`${assets.length} sites`);
  else parts.push("County-wide");
  if (fundingCount > 0) parts.push(`${fundingCount} funding lead${fundingCount === 1 ? "" : "s"}`);
  if (estimateCount > 0) parts.push(`${estimateCount} estimate${estimateCount === 1 ? "" : "s"}`);
  return parts.join(" · ");
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

/** One opportunity: collapsed by default; expand for plan, sites, funding and
 * estimates. */
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
  const plan = opportunity.plan.length > 0 ? opportunity.plan : opportunity.first_steps;
  const sitePlans = new Map(
    (opportunity.site_plans ?? []).map((sitePlan) => [sitePlan.site_id, sitePlan]),
  );
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(main?.id ?? null);
  const selected = assets.find((asset) => asset.id === selectedId) ?? null;
  const selectedPlan: SiteActionPlan | undefined = selected
    ? sitePlans.get(selected.id)
    : undefined;
  const documents = opportunity.documents ?? [];
  const documentsNeeded = selectedPlan?.documents_needed?.length
    ? selectedPlan.documents_needed
    : (opportunity.documents_needed ?? []);
  const detailId = `opportunity-detail-${opportunity.id}`;

  function pickSite(asset: Asset) {
    setSelectedId(asset.id);
    onShowOnMap(asset);
  }

  function toggleExpanded() {
    setExpanded((open) => {
      const next = !open;
      if (next && main) onShowOnMap(main);
      return next;
    });
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
            <section className="opportunity-site-picker">
              <h4>Sites where this could run</h4>
              <p className="muted">
                Click a site to open it on the map and see the course of action, compliance checks,
                and funding for that place.
              </p>
              <ul className="opportunity-sites">
                {assets.map((asset) => {
                  const active = asset.id === selectedId;
                  return (
                    <li key={asset.id}>
                      <button
                        type="button"
                        className={active ? "site-pick site-pick-active" : "site-pick"}
                        onClick={() => pickSite(asset)}
                        aria-pressed={active}
                      >
                        <span
                          className="legend-dot"
                          style={{ background: ASSET_STYLES[asset.type].color }}
                        />
                        <span className="site-pick-text">
                          <span className="site-pick-name">{asset.name}</span>
                          <span className="site-pick-type">{ASSET_STYLES[asset.type].label}</span>
                        </span>
                      </button>
                      {asset.type === "non_powered_dam" && <DamFacts asset={asset} />}
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : (
            <p className="muted">
              {opportunity.solution_id === "farm_digester"
                ? "County-wide: the data gives a count of farms, not their locations, so no site is named."
                : "County-wide: no single site is named. The plan below is for the county as a whole."}
            </p>
          )}

          {selected ? (
            <ActionPlan
              title={`Plan at ${selected.name}`}
              steps={selectedPlan?.course_of_action?.length ? selectedPlan.course_of_action : plan}
              compliance={
                selectedPlan?.compliance?.length
                  ? selectedPlan.compliance
                  : opportunity.compliance
              }
            />
          ) : (
            <ActionPlan
              title="County-wide plan"
              steps={plan}
              compliance={opportunity.compliance}
            />
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

          <FundingAndDocuments
            funding={opportunity.funding}
            documents={documents}
            checklist={documentsNeeded}
            siteLabel={selected?.name ?? null}
            typed={typed}
            byGemini={byGemini}
          />

          <button type="button" className="linkish opportunity-collapse" onClick={toggleExpanded}>
            Hide plan and funding
          </button>
        </div>
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
