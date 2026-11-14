import { useState } from "react";
import { formatNumber, SCOUT_LAYER_LABELS, SITE_STATUS, sourceName } from "../format";
import type {
  Need,
  Observation,
  ScoutRun,
  ScoutStep,
  SiteCandidate,
  SiteDrafts,
} from "../types";
import { EstimateRow } from "./EstimateRow";
import { FundingBadge } from "./FundingBadge";

const STOP_TEXT = {
  target_reached: "Stopped: enough sites were shortlisted.",
  budget_spent: "Stopped: the action budget was spent.",
  no_more_candidates: "Stopped: nothing more was worth inspecting.",
  error: "Stopped early because of an error.",
};

function observationValue(o: Observation): string {
  if (typeof o.value === "boolean") return o.value ? "yes" : "no";
  if (typeof o.value === "number") return `${formatNumber(o.value)}${o.unit ? ` ${o.unit}` : ""}`;
  return o.value.replace(/_/g, " ");
}

function Timeline({ steps, running }: { steps: ScoutStep[]; running: boolean }) {
  return (
    <ol className="scout-timeline" aria-live="polite">
      {steps.map((step) => (
        <li key={step.index} className={`scout-step scout-step-${step.kind}`}>
          <span className="scout-step-kind">{step.kind}</span>
          <span>{step.text}</span>
        </li>
      ))}
      {running && (
        <li className="scout-step scout-step-pending">
          <span className="spinner" aria-hidden="true" />
          <span>Thinking…</span>
        </li>
      )}
    </ol>
  );
}

function SiteCard({
  rank,
  candidate,
  drafts,
  needs,
  onShowOnMap,
}: {
  rank: number;
  candidate: SiteCandidate;
  drafts: SiteDrafts | undefined;
  needs: Need[];
  onShowOnMap: (candidate: SiteCandidate) => void;
}) {
  const [copied, setCopied] = useState(false);
  const addressed = needs.filter((need) => candidate.need_ids.includes(need.id));
  const seen = candidate.observations.filter((o) => o.by_model);
  const recorded = candidate.observations.filter((o) => !o.by_model);

  const copy = (text: string) => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <article className={`card opportunity site site-${candidate.status}`}>
      <header className="opportunity-head">
        <span className="rank">{rank}</span>
        <h3>
          <button
            type="button"
            className="opportunity-title"
            onClick={() => onShowOnMap(candidate)}
            title="Show this building on the map"
          >
            {candidate.name}
          </button>
        </h3>
        <span
          className="score"
          title="Set by code: the priority of the needs addressed, reduced for missing layers or a failed cross-check"
        >
          Score {candidate.score}
        </span>
      </header>

      <div className="chips">
        <span className={`chip chip-site-${candidate.status}`}>{SITE_STATUS[candidate.status]}</span>
        {candidate.layers_used.map((layer) => (
          <span key={layer} className="chip chip-layer">
            {SCOUT_LAYER_LABELS[layer]}
          </span>
        ))}
        {candidate.property_type && (
          <span className="chip chip-other">{candidate.property_type.replace(/_/g, " ")}</span>
        )}
      </div>
      <p className="muted">
        {candidate.address ?? "No street address in the data"} · {candidate.status_reason}
      </p>
      {candidate.agent_note && (
        <p className="note">
          <strong>Why here (Gemini):</strong> {candidate.agent_note}
        </p>
      )}

      {candidate.image_url && (
        <figure className="scout-image">
          <img
            src={candidate.image_url}
            alt={`Aerial view of ${candidate.name} with the parcel, footprint and detected objects outlined`}
            loading="lazy"
          />
          <figcaption className="muted">
            {candidate.image_view === "oblique"
              ? "Vexcel side-angle (oblique) photo. "
              : "Vexcel top-down photo. "}
            Gold: parcel · green: building footprint · magenta: rooftop HVAC · orange: solar panels
            · cyan: cars.
          </figcaption>
        </figure>
      )}

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
      {candidate.solution_ids.length > 0 && (
        <div className="chips">
          <span className="chips-label">Could host</span>
          {candidate.solution_ids.map((id) => (
            <span key={id} className="chip chip-factor">
              {id.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}

      {candidate.checks.length > 0 && (
        <ul className="scout-checks">
          {candidate.checks.map((check) => (
            <li key={check.label} className={check.ok ? "check-ok" : "check-failed"}>
              <span aria-hidden="true">{check.ok ? "✓" : "✗"}</span>
              <span>
                <strong>{check.label}</strong> ({check.layers.map((l) => SCOUT_LAYER_LABELS[l]).join(" vs ")}
                ): {check.detail}
              </span>
            </li>
          ))}
        </ul>
      )}

      {candidate.flags.map((flag) => (
        <div key={flag} className="notice notice-warning">
          {flag}
        </div>
      ))}

      {candidate.estimates.length > 0 && (
        <ul className="estimates">
          {candidate.estimates.map((estimate) => (
            <EstimateRow key={estimate.label} estimate={estimate} />
          ))}
        </ul>
      )}

      <details className="scout-observations">
        <summary>
          What the layers report ({recorded.length} record facts, {seen.length} detections)
        </summary>
        <dl>
          {recorded.map((o) => (
            <div key={o.label}>
              <dt>
                {o.label} <span className="muted">· {SCOUT_LAYER_LABELS[o.layer]}</span>
              </dt>
              <dd>{observationValue(o)}</dd>
            </div>
          ))}
          {seen.map((o) => (
            <div key={o.label}>
              <dt>
                {o.label}{" "}
                <span className="muted">
                  · seen by a model{o.captured ? `, imagery ${o.captured}` : ""}
                </span>
              </dt>
              <dd>{observationValue(o)}</dd>
            </div>
          ))}
        </dl>
      </details>

      {candidate.interior_summary && (
        <blockquote className="scout-summary">
          “{candidate.interior_summary}”
          <footer className="muted">Matterport's own AI summary of the scan, quoted as is.</footer>
        </blockquote>
      )}
      {candidate.tour_url && (
        <details className="scout-tour">
          <summary>View the 3D tour</summary>
          <iframe
            src={candidate.tour_url}
            title={`3D tour of ${candidate.name}`}
            allow="xr-spatial-tracking"
            allowFullScreen
          />
        </details>
      )}

      {drafts && (
        <details className="scout-drafts">
          <summary>Draft owner letter and grant paragraph ({drafts.generated_by})</summary>
          <pre>{drafts.outreach_letter}</pre>
          <button type="button" className="button" onClick={() => copy(drafts.outreach_letter)}>
            {copied ? "Copied" : "Copy letter"}
          </button>
          <p className="note">{drafts.grant_paragraph}</p>
        </details>
      )}
    </article>
  );
}

interface Props {
  run: ScoutRun | null;
  needs: Need[];
  /** The opportunity this scout is scoped to. */
  title: string;
  onStart: () => void;
  onShowOnMap: (candidate: SiteCandidate) => void;
}

/** Site Scout for one opportunity: start button, the live step timeline, then the sites. */
export function ScoutPanel({ run, needs, title, onStart, onShowOnMap }: Props) {
  if (!run) {
    return (
      <div className="scout-intro">
        <p className="muted">
          Find specific commercial buildings that could host <strong>{title}</strong>. An agent
          searches CoStar property records and the Matterport 3D scan library, inspects each roof
          from Vexcel aerial imagery, and checks the layers against each other. Code measures and
          scores every site; Gemini only chooses where to look.
        </p>
        <button type="button" className="button" onClick={onStart}>
          Scout buildings for this
        </button>
      </div>
    );
  }
  const result = run.result;
  const best = bestSites(run);
  // The leads depend on the project type and the county, not the building,
  // so they are the same for every site and are shown once.
  const leads = [...new Map(best.flatMap((c) => c.funding).map((f) => [f.program_id, f])).values()];
  const failed = (result?.source_status ?? []).filter((s) => !s.ok);
  const drafts = new Map((result?.drafts ?? []).map((d) => [d.building_id, d]));
  return (
    <div className="scout">
      {run.error && (
        <div className="notice notice-warning" role="alert">
          <strong>The scout stopped.</strong> {run.error}
          <div>
            <button type="button" className="button" onClick={onStart}>
              Try again
            </button>
          </div>
        </div>
      )}
      {result?.provider === "fake" && (
        <div className="notice notice-warning">
          Placeholder building data: the vendor connections are not set up on this server.
        </div>
      )}
      {result?.generated_by === "rules" && (
        <div className="notice notice-info">
          Gemini was unavailable, so the fixed rules chose the actions.
        </div>
      )}
      {failed.length > 0 && (
        <div className="notice notice-warning">
          <strong>{failed.length === 1 ? "One layer" : `${failed.length} layers`} did not load.</strong>
          <ul>
            {failed.map((s) => (
              <li key={s.source}>
                {sourceName(s.source)}
                {s.message && `: ${s.message}`}
              </li>
            ))}
          </ul>
        </div>
      )}

      <Timeline steps={run.steps} running={run.running} />
      {result && <p className="muted">{STOP_TEXT[result.stopped_because]}</p>}

      {best.length > 0 && (
        <p className="muted">
          The {best.length === 1 ? "best site" : `${best.length} best sites`}, measured from vendor
          geometry and scored by code. Capacity, energy and household figures are estimates;
          detections are what a model saw in one image. Click a name to see its photo.
        </p>
      )}
      {result && best.length === 0 && (
        <p className="muted">
          No building could host this. {run.candidates.length} were inspected; the timeline says
          why each was set aside.
        </p>
      )}
      {best.map((candidate, i) => (
        <SiteCard
          key={candidate.building_id}
          rank={i + 1}
          candidate={candidate}
          drafts={drafts.get(candidate.building_id)}
          needs={needs}
          onShowOnMap={onShowOnMap}
        />
      ))}
      {best.length > 0 && (
        <section className="funding-list">
          <h4>Funding leads for these sites</h4>
          {leads.length === 0 ? (
            <p className="muted">No program in our funding table matches these projects yet.</p>
          ) : (
            leads.map((f) => <FundingBadge key={f.program_id} funding={f} />)
          )}
        </section>
      )}
    </div>
  );
}

/** The three best sites: anything not rejected, shortlisted before low
 * confidence, then by score. Each card carries its status, so a low-confidence
 * site is shown as such rather than hidden. */
export function bestSites(run: ScoutRun): SiteCandidate[] {
  return bestCandidates(run.result?.candidates ?? run.candidates);
}

export function bestCandidates(candidates: SiteCandidate[]): SiteCandidate[] {
  const order = { shortlisted: 0, low_confidence: 1, rejected: 2 };
  return candidates
    .filter((c) => c.status !== "rejected")
    .sort((a, b) => order[a.status] - order[b.status] || b.score - a.score)
    .slice(0, 3);
}
