import { formatNumber } from "../format";
import type { Estimate } from "../types";

/** One labelled estimate: the figure, the Estimate tag, and its assumptions. */
export function EstimateRow({ estimate }: { estimate: Estimate }) {
  return (
    <li className="estimate">
      <div className="estimate-line">
        <span className="estimate-label">{estimate.label}</span>
        <span className="estimate-value">
          {formatNumber(estimate.value)} {estimate.unit}
        </span>
        {/* The tag is on every estimate; assumptions show on hover and on click. */}
        <span className="tag tag-estimate" title={estimate.assumptions.join("\n")}>
          Estimate
        </span>
      </div>
      <details className="assumptions">
        <summary>Assumptions</summary>
        <ul>
          {estimate.assumptions.map((assumption) => (
            <li key={assumption}>{assumption}</li>
          ))}
        </ul>
      </details>
    </li>
  );
}
