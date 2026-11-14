import { formatDate, FUNDING_STATUS } from "../format";
import type { FundingMatch } from "../types";

/**
 * One funding lead. The status and the date it was checked are always shown,
 * and a program that is `uncertain` or `unverified` is never presented as
 * available.
 */
export function FundingBadge({ funding }: { funding: FundingMatch }) {
  const status = FUNDING_STATUS[funding.status];
  return (
    <div className={`funding funding-${funding.status}`}>
      <div className="funding-head">
        <span className="funding-status">{status.label}</span>
        <span className="funding-name">
          {funding.source_url ? (
            <a href={funding.source_url} target="_blank" rel="noreferrer">
              {funding.name}
            </a>
          ) : (
            funding.name
          )}
        </span>
      </div>
      <div className="funding-meta">
        {funding.agency}
        {funding.deadline && ` · Deadline ${formatDate(funding.deadline)}`}
        {" · "}
        {funding.verified_on ? `Checked ${formatDate(funding.verified_on)}` : "Never checked"}
      </div>
      <p className="funding-terms">{funding.key_terms}</p>
      {!status.available && (
        <p className="funding-warning">
          {funding.status === "unverified"
            ? "This program has not been verified. Do not treat it as available."
            : "This program may not be taking applications. Confirm with the agency."}
        </p>
      )}
    </div>
  );
}
