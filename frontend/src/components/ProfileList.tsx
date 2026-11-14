import { formatShare, formatWhole } from "../format";
import type { ProfileCategory, ProfileFact, ProfileUnit } from "../types";

const CATEGORY_LABELS: Record<ProfileCategory, string> = {
  workforce: "Workforce and skills",
  economy: "Local economy",
  community: "Community",
  climate: "Climate",
  geography: "Land and geography",
};
const CATEGORY_ORDER = Object.keys(CATEGORY_LABELS) as ProfileCategory[];

function formatProfileValue(value: number, unit: ProfileUnit): string {
  switch (unit) {
    case "share":
      return formatShare(value);
    case "years":
      return `${value.toFixed(1)} years`;
    case "degree_days_f":
      return `${formatWhole(value)} degree days`;
    case "days_per_year": {
      const days = value.toFixed(0);
      return `${days} ${days === "1" ? "day" : "days"} a year`;
    }
    case "inches_per_year":
      return `${value.toFixed(0)} inches a year`;
    case "m_per_s":
      return `${value.toFixed(1)} m/s`;
    case "feet":
      return `${formatWhole(value)} feet`;
    case "degrees":
      return `${value.toFixed(1)} degrees`;
    case "percentile":
      return `${ordinal(Math.round(value * 100))} percentile`;
  }
}

function ordinal(n: number): string {
  const suffix =
    n % 100 >= 10 && n % 100 <= 20 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th");
  return `${n}${suffix}`;
}

/** County and national figures as two bars on a shared scale. */
function Comparison({ fact }: { fact: ProfileFact }) {
  const county = formatProfileValue(fact.value, fact.unit);
  if (fact.national_value === null) return <p className="need-figure">{county}</p>;
  const max = Math.max(fact.value, fact.national_value) || 1;
  const width = (value: number) => `${Math.max((value / max) * 100, 2)}%`;
  return (
    <dl className="comparison">
      <dt>This county</dt>
      <dd>
        <span className="bar-track">
          <span className="bar bar-county" style={{ width: width(fact.value) }} />
        </span>
        <span className="bar-value">{county}</span>
      </dd>
      <dt>United States</dt>
      <dd>
        <span className="bar-track">
          <span className="bar bar-national" style={{ width: width(fact.national_value) }} />
        </span>
        <span className="bar-value">{formatProfileValue(fact.national_value, fact.unit)}</span>
      </dd>
    </dl>
  );
}

export function ProfileList({ profile }: { profile: ProfileFact[] }) {
  if (profile.length === 0) {
    return <p className="muted">No profile figures loaded for this county.</p>;
  }
  return (
    <>
      <p className="muted">
        What the county has to work with. These figures are context: they do not flag needs or
        change rankings, but the suggested solutions take them into account.
      </p>
      {CATEGORY_ORDER.map((category) => {
        const facts = profile.filter((fact) => fact.category === category);
        if (facts.length === 0) return null;
        return (
          <section key={category}>
            <h3>{CATEGORY_LABELS[category]}</h3>
            <ul className="need-list">
              {facts.map((fact) => (
                <li key={fact.id} className="card need">
                  <header className="need-head">
                    <h3>{fact.label}</h3>
                  </header>
                  <Comparison fact={fact} />
                  {fact.note && <p className="need-detail">{fact.note}</p>}
                  <footer className="need-foot">{fact.source}</footer>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </>
  );
}
