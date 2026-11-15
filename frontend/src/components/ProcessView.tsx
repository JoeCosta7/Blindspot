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
  onTarget: (programId: string) => void;
}

interface PlacedStep extends ProcessStep {
  phaseId: string;
  phaseTitle: string;
}

/** "Jan 12, 2027" from an ISO date, read as a calendar day (no time zone shift). */
function formatDay(iso: string): string {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

function daysBetween(fromIso: string, toIso: string): number {
  return Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 86_400_000);
}

function DueChip({ step, today }: { step: ProcessStep; today: string | null }) {
  if (!step.due) return <span className="due-chip undated">After award</span>;
  if (step.late) return <span className="due-chip late">Overdue: do now</span>;
  const days = today ? daysBetween(today, step.due) : null;
  return (
    <span className={days !== null && days <= 14 ? "due-chip soon" : "due-chip"}>
      Do by {formatDay(step.due)}
      {days !== null && ` · ${days} days`}
    </span>
  );
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
  // [Bracketed] items are what staff still have to supply.
  const blanks = (text.match(/\[[^\]\n]*\]/g) ?? []).length;
  return (
    <div className="pack-section">
      <div className="pack-section-head">
        <h4>{section.title}</h4>
        <span className="muted">
          {edited ? "edited" : section.generated_by === "gemini" ? "worded by Gemini" : "draft"}
        </span>
        {blanks > 0 && (
          <span className="blank-count" title="Items in [brackets] for staff to fill in">
            {blanks} blank{blanks === 1 ? "" : "s"} to fill
          </span>
        )}
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
        rows={Math.max(10, Math.min(28, Math.ceil(text.length / 80) + text.split("\n").length))}
      />
    </div>
  );
}

/** "Get it moving": one next step at the centre, its draft attached; the full
 * list, the analysis ideas and the document pack are one click away. */
export function ProcessView({
  request,
  done,
  edits,
  onToggle,
  onEdit,
  onReset,
  onTarget,
}: Props) {
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
  }, [request.opportunity.id, siteKey, request.target_program_id]);

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
  const byDue = (a: PlacedStep, b: PlacedStep) =>
    Number(b.late) - Number(a.late) || (a.due ?? "9999").localeCompare(b.due ?? "9999");
  const open = steps.filter((s) => !done.includes(s.id)).sort(byDue);
  const current =
    steps.find((s) => s.id === focusId) ??
    open.find((s) => !skipped.includes(s.id)) ??
    open[0] ??
    null;
  // The next few after the current one, so parallel work is visible.
  const comingUp = open.filter((s) => s.id !== current?.id && !skipped.includes(s.id)).slice(0, 3);
  const target = plan.target;
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
      <div className="target-bar">
        {target && target.deadline ? (
          <>
            <span>Aiming for</span>
            {plan.targets.length > 1 ? (
              <select value={target.program_id} onChange={(e) => onTarget(e.target.value)}>
                {plan.targets.map((t) => (
                  <option key={t.program_id} value={t.program_id}>
                    {t.name}
                  </option>
                ))}
              </select>
            ) : (
              <strong>{target.name}</strong>
            )}
            <span className="target-deadline">
              closes {formatDay(target.deadline)}
              {plan.today && ` · ${daysBetween(plan.today, target.deadline)} days left`}
            </span>
          </>
        ) : (
          <span className="muted">
            No open funding deadline is known, so steps are in order without dates.
          </span>
        )}
      </div>

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
          <div className="next-step-top">
            <span className="next-step-phase">{current.phaseTitle}</span>
            <DueChip step={current} today={plan.today} />
          </div>
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

      {comingUp.length > 0 && (
        <section className="coming-up" aria-label="Coming up">
          <h4>Coming up</h4>
          <ul>
            {comingUp.map((s) => (
              <li key={s.id}>
                <button type="button" className="step-title" onClick={() => setFocusId(s.id)}>
                  {s.title}
                </button>
                <span className="muted">{s.who}</span>
                <DueChip step={s} today={plan.today} />
              </li>
            ))}
          </ul>
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
                      <DueChip step={step} today={plan.today} />
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
        {CAVEAT_LINE} Do-by dates use our own lead-time assumptions.
      </p>
      {/* What Print shows: the pack as plain text, with the official's edits. */}
      <pre className="pack-print">{markdown}</pre>
    </div>
  );
}
