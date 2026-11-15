import { useEffect, useState } from "react";
import type { Asset, CountyReport, Opportunity, ScoutRun, SiteCandidate } from "../types";
import { loadWorkspace, saveWorkspace, type WorkspaceState } from "../workspaceStore";
import { ProcessView } from "./ProcessView";
import { bestSites, SiteCard } from "./ScoutPanel";

interface Props {
  report: CountyReport;
  opportunity: Opportunity;
  /** Site Scout can look for places for this opportunity here. */
  canScout: boolean;
  run: ScoutRun | null;
  onStartScout: () => void;
  onShowCandidate: (candidate: SiteCandidate) => void;
  onShowAsset: (asset: Asset) => void;
  onBack: () => void;
}

const NO_SITE = "none";

/** One opportunity, full size: choose a site, then get the government process moving.
 * Progress is kept in this browser per county and opportunity. */
export function OpportunityWorkspace({
  report,
  opportunity,
  canScout,
  run,
  onStartScout,
  onShowCandidate,
  onShowAsset,
  onBack,
}: Props) {
  const [ws, setWs] = useState<WorkspaceState>(() => loadWorkspace(report.fips, opportunity.id));
  useEffect(() => saveWorkspace(report.fips, opportunity.id, ws), [report.fips, opportunity.id, ws]);
  const update = (change: Partial<WorkspaceState>) => setWs((s) => ({ ...s, ...change }));
  // Report sites stay hidden until asked for, like Site Scout's.
  const [revealed, setRevealed] = useState(false);

  const needs = report.needs.filter((n) => opportunity.need_ids.includes(n.id));
  const candidates = run ? bestSites(run) : [];
  const assetsById = new Map(report.assets.map((a) => [a.id, a]));
  const reportSites = opportunity.asset_ids
    .map((id) => assetsById.get(id))
    .filter((a): a is Asset => a !== undefined)
    .slice(0, 3);
  const countyWide = opportunity.asset_ids.length === 0 && !canScout;
  const showReportSites = revealed && candidates.length === 0;
  const chosenCandidate = candidates.find((c) => c.building_id === ws.siteId) ?? null;
  const chosenAsset = reportSites.find((a) => a.id === ws.siteId) ?? null;
  const chosenName =
    ws.siteId === NO_SITE
      ? "No single site"
      : (chosenCandidate?.name ?? chosenAsset?.name ?? null);

  const choose = (siteId: string) => update({ siteId, stage: "process" });

  return (
    <section className="workspace" aria-label={`Workspace for ${opportunity.title}`}>
      <header className="workspace-head">
        <button type="button" className="link-button" onClick={onBack}>
          ← Back to the map
        </button>
        <h2>{opportunity.title}</h2>
        <div className="chips">
          {needs.map((n) => (
            <span key={n.id} className={`chip chip-${n.level}`}>
              {n.label}
            </span>
          ))}
          <span className="chip chip-other">Score {opportunity.score}</span>
          <span className="chip chip-other">{opportunity.funding.length} funding leads</span>
        </div>
        {opportunity.summary && <p className="note">{opportunity.summary}</p>}
        <ol className="stepper">
          <li className={ws.stage === "site" ? "active" : "done"}>
            <button type="button" onClick={() => update({ stage: "site" })}>
              ① Choose a site{chosenName ? `: ${chosenName}` : ""}
            </button>
          </li>
          <li className={ws.stage === "process" ? "active" : undefined}>
            <button
              type="button"
              disabled={ws.siteId === null}
              onClick={() => update({ stage: "process" })}
            >
              ② Get it moving
            </button>
          </li>
        </ol>
      </header>

      {ws.stage === "site" && (
        <div className="workspace-body">
          {!canScout && reportSites.length > 0 && !revealed && (
            <div className="card workspace-callout">
              <p>Show the places the county report found for this project.</p>
              <button type="button" className="button" onClick={() => setRevealed(true)}>
                Find sites
              </button>
            </div>
          )}
          {canScout && !run && (
            <div className="card workspace-callout">
              <p>
                Site Scout looks for specific places that could host this, from the county report,
                CoStar records, Matterport scans and Vexcel imagery, and keeps the 3 best.
              </p>
              <button type="button" className="button" onClick={onStartScout}>
                Find sites
              </button>
            </div>
          )}
          {run?.running && (
            <div className="card workspace-callout" aria-live="polite">
              <span className="spinner" aria-hidden="true" />{" "}
              {run.steps.at(-1)?.text ?? "Starting…"}
            </div>
          )}
          {run?.error && <div className="notice notice-warning">{run.error}</div>}

          {candidates.length > 0 && (
            <div className="site-columns">
              {candidates.map((c, i) => (
                <div
                  key={c.building_id}
                  className={ws.siteId === c.building_id ? "site-column chosen" : "site-column"}
                >
                  <SiteCard
                    rank={i + 1}
                    candidate={c}
                    needs={report.needs}
                    onShowOnMap={onShowCandidate}
                  />
                  <button type="button" className="button" onClick={() => choose(c.building_id)}>
                    Choose this site
                  </button>
                </div>
              ))}
            </div>
          )}

          {showReportSites && reportSites.length > 0 && (
            <div className="site-columns">
              {reportSites.map((a, i) => {
                const plan = opportunity.site_plans.find((p) => p.site_id === a.id);
                return (
                  <div
                    key={a.id}
                    className={ws.siteId === a.id ? "card site-column chosen" : "card site-column"}
                  >
                    <h3>
                      {i + 1}. {a.name}
                    </h3>
                    <p className="muted">
                      {a.type.replace(/_/g, " ")} · {a.source}
                    </p>
                    {plan && plan.course_of_action.length > 0 && (
                      <ol className="site-plan">
                        {plan.course_of_action.slice(0, 4).map((step) => (
                          <li key={step}>{step}</li>
                        ))}
                      </ol>
                    )}
                    <button type="button" className="link-button" onClick={() => onShowAsset(a)}>
                      Show on map
                    </button>
                    <button type="button" className="button" onClick={() => choose(a.id)}>
                      Choose this site
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {countyWide && (
            <div className="card workspace-callout">
              <p>This is a county-wide program: it does not need a single site.</p>
              <button type="button" className="button" onClick={() => choose(NO_SITE)}>
                Continue
              </button>
            </div>
          )}
        </div>
      )}

      {ws.stage === "process" && ws.siteId !== null && (
        <ProcessView
          request={{
            fips: report.fips,
            county_name: report.name,
            state: report.state,
            opportunity,
            needs,
            site: chosenCandidate,
            site_asset: chosenAsset,
            target_program_id: ws.target,
          }}
          done={ws.done}
          edits={ws.edits}
          onToggle={(id) =>
            update({
              done: ws.done.includes(id) ? ws.done.filter((d) => d !== id) : [...ws.done, id],
            })
          }
          onEdit={(id, text) => update({ edits: { ...ws.edits, [id]: text } })}
          onTarget={(target) => update({ target })}
          onReset={(id) => {
            const { [id]: _dropped, ...rest } = ws.edits;
            update({ edits: rest });
          }}
        />
      )}
    </section>
  );
}
