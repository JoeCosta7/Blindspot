import { ASSET_STYLES, FACTOR_CATEGORY_LABELS } from "../format";
import type { AssetType, Factor, FactorCategory } from "../types";

interface Props {
  factors: Factor[];
  /** Asset types searched for and not found. */
  missingAssetTypes: AssetType[];
}

/** The assets and factors section: what the county has to work with. The
 * opportunities are built from these and from the mapped sites. */
export function FactorList({ factors, missingAssetTypes }: Props) {
  if (factors.length === 0) {
    return <p className="muted">No facts about this county could be loaded.</p>;
  }
  // The backend sends factors grouped by category, in display order.
  const groups: [FactorCategory, Factor[]][] = [];
  for (const factor of factors) {
    const last = groups[groups.length - 1];
    if (last && last[0] === factor.category) last[1].push(factor);
    else groups.push([factor.category, [factor]]);
  }

  return (
    <>
      <p className="muted">
        What the county has to work with: its climate, land, buildings, workforce and more. The
        opportunities are built from these facts and from the sites on the map.
      </p>
      {groups.map(([category, items]) => (
        <section key={category} className="card factor-group">
          <h3>{FACTOR_CATEGORY_LABELS[category]}</h3>
          <dl className="factor-list">
            {items.map((factor) => (
              <div key={factor.id} className="factor">
                <dt>{factor.label}</dt>
                <dd>
                  <span className="factor-value">{factor.value}</span>
                  {factor.comparison && (
                    <span className="factor-comparison"> · {factor.comparison}</span>
                  )}
                  {factor.detail && <p className="factor-detail">{factor.detail}</p>}
                  <p className="factor-detail">{factor.source}</p>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      {missingAssetTypes.length > 0 && (
        <p className="muted">
          Looked for and not found:{" "}
          {missingAssetTypes.map((type) => ASSET_STYLES[type].plural.toLowerCase()).join(", ")}.
        </p>
      )}
    </>
  );
}
