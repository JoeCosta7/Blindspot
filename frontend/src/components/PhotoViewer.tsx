import { useEffect, useState } from "react";
import { SITE_STATUS } from "../format";
import type { ImageOption, ScanRoom, SiteCandidate } from "../types";

interface Props {
  candidate: SiteCandidate;
  onClose: () => void;
  /** Extra class, e.g. to sit over the workspace. */
  className?: string;
}

function m2(value: number | null): string {
  return value === null ? "" : `${Math.round(value).toLocaleString()} m²`;
}

/** Rooms read from the Matterport API: name, floor area, highest ceiling. */
export function ScanRooms({ rooms, floors }: { rooms: ScanRoom[]; floors: number | null }) {
  if (rooms.length === 0) return <p className="muted">The scan reports no room sizes.</p>;
  return (
    <table className="scan-rooms">
      <caption>
        From the Matterport 3D scan{floors ? ` · ${floors} floor${floors === 1 ? "" : "s"}` : ""}
      </caption>
      <thead>
        <tr>
          <th>Room</th>
          <th>Floor area</th>
          <th>Ceiling</th>
        </tr>
      </thead>
      <tbody>
        {rooms.map((r, i) => (
          <tr key={`${r.label ?? "room"}-${i}`}>
            <td>{r.label || "Unnamed space"}</td>
            <td>{m2(r.area_m2)}</td>
            <td>{r.ceiling_m !== null ? `${r.ceiling_m.toFixed(1)} m` : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
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
  // Photos (Vexcel) or the walk-through (Matterport), when the site has a scan.
  const [tab, setTab] = useState<"photos" | "tour">("photos");
  const tourUrl = candidate.tour_url
    ? `${candidate.tour_url}${candidate.tour_url.includes("?") ? "&" : "?"}play=1&qs=1`
    : null;
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
        {tourUrl && (
          <div className="viewer-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "photos"}
              className={tab === "photos" ? "active" : undefined}
              onClick={() => setTab("photos")}
            >
              Aerial photos
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "tour"}
              className={tab === "tour" ? "active" : undefined}
              onClick={() => setTab("tour")}
            >
              Walk through inside (3D)
            </button>
          </div>
        )}
        <button type="button" className="photo-viewer-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </header>
      {tab === "tour" && tourUrl ? (
        <div className="tour-stage">
          <iframe
            src={tourUrl}
            title={`3D tour of ${candidate.name}`}
            allow="xr-spatial-tracking; fullscreen"
            allowFullScreen
          />
          <aside>
            <ScanRooms rooms={candidate.scan_rooms} floors={candidate.scan_floors} />
            {candidate.interior_summary && (
              <p className="muted">
                Matterport's summary: “{candidate.interior_summary}”
              </p>
            )}
          </aside>
        </div>
      ) : current ? (
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
