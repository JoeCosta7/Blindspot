import type { AnalyzeRequest, AnalyzeResponse, CountyCollection } from "./types";

/** `?fixture=1` in the page URL serves saved reports: the demo works offline. */
export const FIXTURE_MODE = new URLSearchParams(window.location.search).get("fixture") === "1";

async function readError(resp: Response): Promise<string> {
  try {
    const body: unknown = await resp.json();
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // not JSON: fall through
  }
  return `The server answered ${resp.status}.`;
}

export async function fetchCounties(): Promise<CountyCollection> {
  const resp = await fetch("/api/counties.geojson");
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as CountyCollection;
}

export async function analyze(
  request: AnalyzeRequest,
  signal?: AbortSignal,
): Promise<AnalyzeResponse> {
  const resp = await fetch(`/api/analyze${FIXTURE_MODE ? "?fixture=1" : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as AnalyzeResponse;
}
