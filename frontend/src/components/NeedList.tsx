import { formatNeedValue, THEME_LABELS } from "../format";
import type { Need, NeedId } from "../types";

/** Needs measured for the whole state, because no county figure is published. */
const STATE_LEVEL: ReadonlySet<NeedId> = new Set(["high_electricity_cost", "carbon_intensive_grid"]);

/** County and national figures as two bars on a shared scale. */
function Comparison({ need }: { need: Need }) {
  const county = formatNeedValue(need.value, need.unit);
  const national = formatNeedValue(need.national_value, need.unit);
  if (county === null) return null;
  if (need.value === null || need.national_value === null || national === null) {
    return need.unit === "count" ? null : <p className="need-figure">{county}</p>;
  }
  const max = Math.max(need.value, need.national_value) || 1;
  const width = (value: number) => `${Math.max((value / max) * 100, 2)}%`;
  return (
    <dl className="comparison">
      <dt>{STATE_LEVEL.has(need.id) ? "State average" : "This county"}</dt>
      <dd>
        <span className="bar-track">
          <span className="bar bar-county" style={{ width: width(need.value) }} />
        </span>
        <span className="bar-value">{county}</span>
      </dd>
      <dt>United States</dt>
      <dd>
        <span className="bar-track">
          <span className="bar bar-national" style={{ width: width(need.national_value) }} />
        </span>
        <span className="bar-value">{national}</span>
      </dd>
    </dl>
  );
}

export function NeedList({ needs }: { needs: Need[] }) {
  if (needs.length === 0) {
    return (
      <p className="muted">
        No needs were flagged from the data that loaded. A need is flagged when the county is at
        least 10% worse than the national figure.
      </p>
    );
  }
  return (
    <>
      <p className="muted">
        Ordered by priority: how far the county is from the US figure (severity), weighted by how
        much that kind of need counts for sustainability.
      </p>
      <ul className="need-list">
        {needs.map((need) => (
          <li key={need.id} className="card need">
            <header className="need-head">
              <h3>{need.label}</h3>
              <span className={`chip chip-${need.level}`}>
                {need.level === "high" ? "High" : "Elevated"}
              </span>
            </header>
            <Comparison need={need} />
            {need.detail && <p className="need-detail">{need.detail}</p>}
            <footer className="need-foot">
              {THEME_LABELS[need.theme]} · Priority {need.priority} · Severity {need.severity} of
              100 · {need.source}
            </footer>
          </li>
        ))}
      </ul>
    </>
  );
}
