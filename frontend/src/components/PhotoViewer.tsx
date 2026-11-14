import { useEffect, useState } from "react";
import { SITE_STATUS } from "../format";
import type { ImageOption, SiteCandidate } from "../types";

interface Props {
  candidate: SiteCandidate;
  onClose: () => void;
  /** Extra class, e.g. to sit over the workspace. */
  className?: string;
}

/** The Vexcel photos of a scouted site, large, over the map. Arrow keys or
 * the buttons cycle the side-angle directions; Escape closes. */
export function PhotoViewer({ candidate, onClose, className }: Props) {
  // The eager photo first, then the other directions, fetched when shown.
  const options: ImageOption[] =
    candidate.image_options.length > 0
      ? candidate.image_options
      : candidate.image_url
        ? [{ label: candidate.image_view === "oblique" ? "Side view" : "Top-down", url: candidate.image_url }]
        : [];
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const current = options[index] ?? null;
  const step = (delta: number) => {
    if (options.length < 2) return;
    setLoading(true);
    setIndex((i) => (i + delta + options.length) % options.length);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowRight") step(1);
      if (event.key === "ArrowLeft") step(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // step reads options via closure; options only change with the candidate, which remounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose, options.length]);

  const capacity = candidate.estimates.find((e) => e.label === "Capacity");
  return (
    <div className={className ? `photo-viewer ${className}` : "photo-viewer"} role="dialog" aria-label={`Aerial photo of ${candidate.name}`}>
      <header className="photo-viewer-head">
        <div>
          <h3>{candidate.name}</h3>
          <p className="muted">
            {candidate.address ?? "No street address in the data"} · {SITE_STATUS[candidate.status]}
            {" · "}Score {candidate.score}
            {capacity &&
              ` · about ${Math.round(capacity.value).toLocaleString()} ${capacity.unit} (estimate)`}
          </p>
        </div>
        <button type="button" className="photo-viewer-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </header>
      {current ? (
        <div className="photo-viewer-stage">
          {loading && <span className="spinner photo-viewer-spinner" aria-hidden="true" />}
          <img
            key={current.url}
            src={current.url}
            alt={`${current.label} aerial photo of ${candidate.name}`}
            onLoad={() => setLoading(false)}
            onError={() => setLoading(false)}
          />
        </div>
      ) : (
        <p className="muted">No aerial photo was fetched for this site.</p>
      )}
      <footer className="photo-viewer-foot">
        {options.length > 1 ? (
          <div className="photo-viewer-nav" role="group" aria-label="Viewing direction">
            <button type="button" onClick={() => step(-1)} aria-label="Previous direction">
              ←
            </button>
            <span>
              {current?.label} · {index + 1} of {options.length}
            </span>
            <button type="button" onClick={() => step(1)} aria-label="Next direction">
              →
            </button>
          </div>
        ) : (
          <span>{current?.label}</span>
        )}
        <span className="muted">
          Vexcel imagery · gold: parcel · green: footprint · magenta: rooftop HVAC · orange: solar ·
          cyan: cars
          {candidate.tour_url && (
            <>
              {" · "}
              <a href={candidate.tour_url} target="_blank" rel="noreferrer">
                Open the 3D tour
              </a>
            </>
          )}
        </span>
      </footer>
    </div>
  );
}
