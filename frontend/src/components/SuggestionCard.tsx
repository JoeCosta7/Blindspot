import { ASSET_STYLES } from "../format";
import type { Asset, Need, SuggestedSolution } from "../types";

interface Props {
  suggestion: SuggestedSolution;
  /** The sites the suggestion names, looked up on the map. */
  assets: Asset[];
  needs: Need[];
  onShowOnMap: (asset: Asset) => void;
}

/** A solution proposed by Gemini. Deliberately has no score, estimate or
 * funding: those come only from the rule-based opportunities. */
export function SuggestionCard({ suggestion, assets, needs, onShowOnMap }: Props) {
  const addressed = needs.filter((need) => suggestion.need_ids.includes(need.id));
  return (
    <article className="card opportunity">
      <header className="opportunity-head">
        <h3>{suggestion.title}</h3>
        <span
          className="tag tag-ai"
          title="Proposed by Gemini. Not checked for feasibility, cost or funding."
        >
          AI suggestion
        </span>
      </header>

      <p className="note">{suggestion.summary}</p>
      <p className="note">
        <strong>Why here:</strong> {suggestion.rationale}
      </p>

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

      {assets.length > 0 ? (
        <ul className="suggestion-sites">
          {assets.map((asset) => (
            <li key={asset.id}>
              <button type="button" className="link-button" onClick={() => onShowOnMap(asset)}>
                <span
                  className="legend-dot"
                  style={{ background: ASSET_STYLES[asset.type].color }}
                />
                {asset.name}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">County-wide: no single site is named.</p>
      )}

      {suggestion.first_steps.length > 0 && (
        <section className="first-steps">
          <h4>First steps</h4>
          <ol>
            {suggestion.first_steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}
