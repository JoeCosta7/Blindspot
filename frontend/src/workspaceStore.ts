/** What the official has done in an opportunity's workspace, kept in this
 * browser (localStorage) per county and opportunity. Storage can be
 * unavailable, so every access is guarded and failures are silent. */

export type WorkspaceStage = "site" | "process";

export interface WorkspaceState {
  stage: WorkspaceStage;
  /** A Site Scout building id or a report asset id; "none" for county-wide. */
  siteId: string | null;
  done: string[];
  edits: Record<string, string>;
}

const EMPTY: WorkspaceState = { stage: "site", siteId: null, done: [], edits: {} };

const key = (fips: string, opportunityId: string) => `workspace:${fips}:${opportunityId}`;

export function loadWorkspace(fips: string, opportunityId: string): WorkspaceState {
  try {
    const raw = localStorage.getItem(key(fips, opportunityId));
    return raw ? { ...EMPTY, ...(JSON.parse(raw) as Partial<WorkspaceState>) } : EMPTY;
  } catch {
    return EMPTY;
  }
}

export function saveWorkspace(fips: string, opportunityId: string, state: WorkspaceState) {
  try {
    localStorage.setItem(key(fips, opportunityId), JSON.stringify(state));
  } catch {
    // not saved; the workspace still works for this visit
  }
}
