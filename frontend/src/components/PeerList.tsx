import { formatDollars, formatWhole } from "../format";
import type { CountyReport, PeerCounty } from "../types";

const formatDensity = (perSqMile: number): string =>
  `${perSqMile < 10 ? perSqMile.toFixed(1) : formatWhole(perSqMile)} per sq mi`;

function Figures({
  population,
  income,
  density,
}: {
  population: number | null;
  income: number | null;
  density: number | null;
}) {
  return (
    <dl className="stats">
      <div>
        <dt>Population</dt>
        <dd>{population !== null ? formatWhole(population) : "Not known"}</dd>
      </div>
      <div>
        <dt>Median household income</dt>
        <dd>{income !== null ? formatDollars(income) : "Not known"}</dd>
      </div>
      <div>
        <dt>People per square mile</dt>
        <dd>{density !== null ? formatDensity(density) : "Not known"}</dd>
      </div>
    </dl>
  );
}

function PeerCard({ peer, onAnalyze }: { peer: PeerCounty; onAnalyze: (fips: string) => void }) {
  const shared = peer.needs.filter((need) => need.shared);
  const own = peer.needs.filter((need) => !need.shared);
  return (
    <li className="card peer">
      <header className="peer-head">
        <h3>
          {peer.name}, {peer.state}
        </h3>
        <button type="button" className="link-button" onClick={() => onAnalyze(peer.fips)}>
          Open its report
        </button>
      </header>
      <Figures
        population={peer.population}
        income={peer.median_household_income}
        density={peer.people_per_sq_mile}
      />
      {shared.length > 0 && (
        <div className="chips">
          <span className="chips-label">Shares your needs:</span>
          {shared.map((need) => (
            <span key={need.id} className="chip chip-shared">
              {need.label}
            </span>
          ))}
        </div>
      )}
      {own.length > 0 && (
        <div className="chips">
          <span className="chips-label">Also has:</span>
          {own.map((need) => (
            <span key={need.id} className="chip chip-other">
              {need.label}
            </span>
          ))}
        </div>
      )}
      {peer.needs.length === 0 && <p className="muted">No Census needs flagged.</p>}
    </li>
  );
}

interface Props {
  report: CountyReport;
  onAnalyze: (fips: string) => void;
}

export function PeerList({ report, onAnalyze }: Props) {
  if (report.peers.length === 0) {
    const status = report.source_status.find((s) => s.source === "peers");
    return (
      <p className="muted">
        {status && !status.ok
          ? "Peer counties could not be loaded for this report."
          : "No peer counties were found: the Census does not publish this county's population, income or land area."}
      </p>
    );
  }
  return (
    <>
      <p className="muted">
        The US counties closest to this one in population, household income and people per square
        mile, closest first. Needs are the Census-based ones only, flagged the same way as this
        county's. Open a peer's report to see what it has and the opportunities found there.
      </p>
      <div className="card peer peer-self">
        <h3>
          {report.name}, {report.state} (this county)
        </h3>
        <Figures
          population={report.population}
          income={report.context.median_household_income}
          density={report.people_per_sq_mile}
        />
      </div>
      <ul className="peer-list">
        {report.peers.map((peer) => (
          <PeerCard key={peer.fips} peer={peer} onAnalyze={onAnalyze} />
        ))}
      </ul>
    </>
  );
}
