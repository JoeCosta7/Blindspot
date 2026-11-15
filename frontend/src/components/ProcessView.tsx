import { useEffect, useState } from "react";
import { fetchProcess } from "../api";
import type { PackSection, ProcessPlan, ProcessRequest, ProcessStep, ProjectDocument } from "../types";

interface Props {
  request: ProcessRequest;
  done: string[];
  edits: Record<string, string>;
  onToggle: (id: string) => void;
  onEdit: (id: string, text: string) => void;
  onReset: (id: string) => void;
}

interface PlacedStep extends ProcessStep {
  phaseId: string;
  phaseTitle: string;
}

const CAVEAT_LINE =
  "Typical steps, not legal advice — confirm with your county attorney and each funding agency.";

function DocLinks({ ids, docs }: { ids: string[]; docs: Map<string, ProjectDocument> }) {
  const found = ids.map((id) => docs.get(id)).filter((d): d is ProjectDocument => !!d);
  if (found.length === 0) return null;
  return (
    <ul className="step-forms">
      {found.map((d) =>
        d.source_url ? (
          <li key={d.id}>
            <a href={d.source_url} target="_blank" rel="noreferrer">
              {d.name}
            </a>
          </li>
        ) : (
          <li key={d.id}>{d.name}</li>
        ),
      )}
    </ul>
  );
}

function SectionEditor({
  section,
  text,
  edited,
  onEdit,
  onReset,
}: {
  section: PackSection;
  text: string;
  edited: boolean;
  onEdit: (text: string) => void;
  onReset: () => void;
}) {
  return (
    <div className="pack-section">
      <div className="pack-section-head">
        <h4>{section.title}</h4>
        <span className="muted">
          {edited ? "edited" : section.generated_by === "gemini" ? "worded by Gemini" : "draft"}
        </span>
        <button
          type="button"
          className="link-button"
          onClick={() => void navigator.clipboard?.writeText(text)}
        >
          Copy
        </button>
        {edited && (
          <button type="button" className="link-button" onClick={onReset}>
            Reset
          </button>
        )}
      </div>
      <textarea
        value={text}
        onChange={(e) => onEdit(e.target.value)}
        rows={Math.min(12, text.split("\n").length + 2)}
      />
    </div>
  );
}

/** "Get it moving": one next step at the centre, its draft attached; the full
 * list, the analysis ideas and the document pack are one click away. */
export function ProcessView({ request, done, edits, onToggle, onEdit, onReset }: Props) {
  const [plan, setPlan] = useState<ProcessPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [focusId, setFocusId] = useState<string | null>(null);
  const siteKey = request.site?.building_id ?? request.site_asset?.id ?? "none";

  useEffect(() => {
    let live = true;
    setPlan(null);
    setError(null);
    fetchProcess(request)
      .then((p) => live && setPlan(p))
      .catch((err: unknown) => live && setError(err instanceof Error ? err.message : "Failed."));
    return () => {
      live = false;
    };
    // The request is rebuilt each render; refetch only when the opportunity or site changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request.opportunity.id, siteKey]);

  if (error) return <div className="notice notice-warning">The plan could not be built. {error}</div>;
  if (!plan) {
    return (
      <div className="workspace-body muted">
        <span className="spinner" aria-hidden="true" /> Building the plan…
      </div>
    );
  }

  const steps: PlacedStep[] = plan.phases.flatMap((p) =>
    p.steps.map((s) => ({ ...s, phaseId: p.id, phaseTitle: p.title })),
  );
  const open = steps.filter((s) => !done.includes(s.id));
  const current =
    steps.find((s) => s.id === focusId) ??
    open.find((s) => !skipped.includes(s.id)) ??
    open[0] ??
    null;
  const docs = new Map(plan.documents.map((d) => [d.id, d]));
  const sections = new Map(plan.sections.map((s) => [s.id, s]));
  const textOf = (s: PackSection) => edits[s.id] ?? s.text;

  const markdown = [
    `# ${request.opportunity.title} — ${request.county_name}, ${request.state}`,
    ...plan.sections.map((s) => `## ${s.title}\n\n${textOf(s)}`),
    "## Forms to gather",
    ...plan.documents.map((d) => `- ${d.name}${d.source_url ? ` (${d.source_url})` : ""}`),
    ...plan.documents_needed.map((d) => `- ${d}`),
    "## Notes",
    ...plan.caveats.map((c) => `- ${c}`),
  ].join("\n\n");

  const download = () => {
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${request.opportunity.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const editor = (s: PackSection) => (
    <SectionEditor
      key={s.id}
      section={s}
      text={textOf(s)}
      edited={edits[s.id] !== undefined}
      onEdit={(text) => onEdit(s.id, text)}
      onReset={() => onReset(s.id)}
    />
  );

  return (
    <div className="workspace-body process-view">
      {/* Progress: one segment per phase, filled by the share done. */}
      <div className="process-progress">
        <ol className="phase-strip">
          {plan.phases.map((p) => {
            const n = p.steps.length;
            const k = p.steps.filter((s) => done.includes(s.id)).length;
            return (
              <li
                key={p.id}
                className={current?.phaseId === p.id ? "phase current" : "phase"}
                title={`${p.title}: ${k} of ${n} done`}
              >
                <span className="phase-bar">
                  <span style={{ width: `${n ? (100 * k) / n : 0}%` }} />
                </span>
                <span className="phase-name">{p.title}</span>
              </li>
            );
          })}
        </ol>
        <span className="muted">
          {steps.length - open.length} of {steps.length} done
        </span>
      </div>

      {current ? (
        <section className="card next-step" aria-label="Next step">
          <span className="next-step-phase">{current.phaseTitle}</span>
          <h3>{current.title}</h3>
          {current.who && <p className="muted">Usually: {current.who}</p>}
          {current.detail && <p>{current.detail}</p>}
          <DocLinks ids={current.document_ids} docs={docs} />
          {current.section_ids
            .map((id) => sections.get(id))
            .filter((s): s is PackSection => !!s)
            .map(editor)}
          <div className="next-step-actions">
            {done.includes(current.id) ? (
              <button type="button" className="button" onClick={() => onToggle(current.id)}>
                Mark not done
              </button>
            ) : (
              <button
                type="button"
                className="button"
                onClick={() => {
                  onToggle(current.id);
                  setFocusId(null);
                }}
              >
                Mark done
              </button>
            )}
            {!done.includes(current.id) && open.length > 1 && (
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  setSkipped((s) => [...s, current.id]);
                  setFocusId(null);
                }}
              >
                Later
              </button>
            )}
          </div>
        </section>
      ) : (
        <section className="card next-step">
          <h3>Every step is checked</h3>
          <p>Print the packet for the board, or download it to share.</p>
          <div className="next-step-actions">
            <button type="button" className="button" onClick={() => window.print()}>
              Print packet
            </button>
          </div>
        </section>
      )}

      <div className="process-links">
        <button type="button" className="link-button" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Hide all steps" : `See all ${steps.length} steps`}
        </button>
        <button type="button" className="link-button" onClick={() => setShowDocs((v) => !v)}>
          {showDocs ? "Hide documents" : "All documents"}
        </button>
      </div>

      {showAll && (
        <section className="process-checklist" aria-label="All steps">
          {plan.phases.map((phase) => (
            <div
              key={phase.id}
              className={current?.phaseId === phase.id ? "process-phase current" : "process-phase"}
            >
              <h4>{phase.title}</h4>
              <ul>
                {phase.steps.map((step) => (
                  <li key={step.id} className={done.includes(step.id) ? "step done" : "step"}>
                    <input
                      type="checkbox"
                      checked={done.includes(step.id)}
                      onChange={() => onToggle(step.id)}
                      aria-label={`Done: ${step.title}`}
                    />
                    <span>
                      <button
                        type="button"
                        className="step-title"
                        onClick={() => setFocusId(step.id)}
                      >
                        {step.title}
                      </button>
                      {step.who && <span className="muted"> · {step.who}</span>}
                      {step.detail && <span className="step-detail">{step.detail}</span>}
                      <DocLinks ids={step.document_ids} docs={docs} />
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}

      {plan.ideas.length > 0 && (
        <details className="process-ideas">
          <summary>Ideas from the analysis ({plan.ideas.length})</summary>
          <ul>
            {plan.ideas.map((i) => (
              <li key={i.id}>{i.title}</li>
            ))}
          </ul>
        </details>
      )}

      {showDocs && (
        <section className="document-pack" aria-label="Document pack">
          <div className="pack-actions">
            <h3>Document pack</h3>
            <button type="button" className="button" onClick={() => window.print()}>
              Print packet
            </button>
            <button type="button" className="button" onClick={download}>
              Download
            </button>
          </div>
          {plan.sections.map(editor)}
          <div className="pack-section">
            <h4>Forms to gather</h4>
            <ul className="pack-forms">
              {plan.documents.map((d) => (
                <li key={d.id}>
                  {d.source_url ? (
                    <a href={d.source_url} target="_blank" rel="noreferrer">
                      {d.name}
                    </a>
                  ) : (
                    d.name
                  )}
                </li>
              ))}
              {plan.documents_needed.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
        </section>
      )}

      <p className="muted process-caveat" title={plan.caveats.join("\n")}>
        {CAVEAT_LINE}
      </p>
      {/* What Print shows: the pack as plain text, with the official's edits. */}
      <pre className="pack-print">{markdown}</pre>
    </div>
  );
}
