import { useEffect, useState } from "react";
import { fetchProcess } from "../api";
import type { ProcessPlan, ProcessRequest } from "../types";

interface Props {
  request: ProcessRequest;
  done: string[];
  edits: Record<string, string>;
  onToggle: (id: string) => void;
  onEdit: (id: string, text: string) => void;
  onReset: (id: string) => void;
}

/** The checklist (left) and the editable document pack (right). */
export function ProcessView({ request, done, edits, onToggle, onEdit, onReset }: Props) {
  const [plan, setPlan] = useState<ProcessPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  const steps = plan.phases.flatMap((p) => p.steps);
  const finished = steps.filter((s) => done.includes(s.id)).length;
  const docsById = new Map(plan.documents.map((d) => [d.id, d]));
  const textOf = (id: string, fallback: string) => edits[id] ?? fallback;

  const markdown = [
    `# ${request.opportunity.title} — ${request.county_name}, ${request.state}`,
    ...plan.sections.map((s) => `## ${s.title}\n\n${textOf(s.id, s.text)}`),
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

  return (
    <div className="workspace-body process">
      <section className="process-checklist" aria-label="Checklist">
        <h3>
          Checklist <span className="muted">· {finished} of {steps.length} done</span>
        </h3>
        {plan.phases.map((phase) => (
          <div key={phase.id} className="process-phase">
            <h4>{phase.title}</h4>
            <ul>
              {phase.steps.map((step) => (
                <li key={step.id} className={done.includes(step.id) ? "step done" : "step"}>
                  <label>
                    <input
                      type="checkbox"
                      checked={done.includes(step.id)}
                      onChange={() => onToggle(step.id)}
                    />
                    <span>
                      <strong>{step.title}</strong>
                      {step.who && <span className="muted"> · {step.who}</span>}
                      {step.detail && <span className="step-detail">{step.detail}</span>}
                      {step.document_ids.map((id) => {
                        const doc = docsById.get(id);
                        if (!doc) return null;
                        return doc.source_url ? (
                          <a key={id} href={doc.source_url} target="_blank" rel="noreferrer">
                            {doc.name}
                          </a>
                        ) : (
                          <span key={id} className="step-doc">
                            {doc.name}
                          </span>
                        );
                      })}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </div>
        ))}
        <ul className="caveats">
          {plan.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      </section>

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
        {plan.sections.map((s) => (
          <div key={s.id} className="pack-section">
            <div className="pack-section-head">
              <h4>{s.title}</h4>
              <span className="muted">
                {edits[s.id] !== undefined
                  ? "edited"
                  : s.generated_by === "gemini"
                    ? "worded by Gemini"
                    : "template"}
              </span>
              <button
                type="button"
                className="link-button"
                onClick={() => void navigator.clipboard?.writeText(textOf(s.id, s.text))}
              >
                Copy
              </button>
              {edits[s.id] !== undefined && (
                <button type="button" className="link-button" onClick={() => onReset(s.id)}>
                  Reset
                </button>
              )}
            </div>
            <textarea
              value={textOf(s.id, s.text)}
              onChange={(e) => onEdit(s.id, e.target.value)}
              rows={Math.min(14, textOf(s.id, s.text).split("\n").length + 2)}
            />
          </div>
        ))}
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
                {d.description && <span className="muted"> · {d.description}</span>}
              </li>
            ))}
            {plan.documents_needed.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </div>
        {/* What Print shows: the pack as plain text, with the official's edits. */}
        <pre className="pack-print">{markdown}</pre>
      </section>
    </div>
  );
}
