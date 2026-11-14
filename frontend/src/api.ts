import type {
  AnalyzeRequest,
  AnalyzeResponse,
  CountyCollection,
  NeedId,
  NeedMapResponse,
  ScoutEvent,
} from "./types";

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

export async function fetchNeedMap(): Promise<NeedMapResponse> {
  const resp = await fetch("/api/need-map");
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as NeedMapResponse;
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

/** Run Site Scout for one opportunity in a county. The response is NDJSON,
 * one event per line; `onEvent` is called for each as it arrives, the last
 * being the result. */
export async function scout(
  fips: string,
  needIds: NeedId[],
  focus: string,
  solutionId: string | null,
  onEvent: (event: ScoutEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`/api/scout${FIXTURE_MODE ? "?fixture=1" : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fips, need_ids: needIds, focus, solution_id: solutionId }),
    signal,
  });
  if (!resp.ok) throw new Error(await readError(resp));
  if (!resp.body) throw new Error("The server sent no stream.");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const emit = (line: string) => {
    const trimmed = line.trim();
    if (trimmed) onEvent(JSON.parse(trimmed) as ScoutEvent);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      emit(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
    }
  }
  emit(buffer);
}
