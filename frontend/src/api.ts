import type {
  AnalyzeEvent,
  AnalyzeRequest,
  AnalyzeResponse,
  CountyCollection,
  NeedId,
  NeedMapResponse,
  ScoutEvent,
  ProcessPlan,
  ProcessRequest,
  SourceStatus,
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

/** Analyze a county or box. Streams each source as it finishes when
 * `onSource` is set (server `?progress=1`); otherwise waits for one JSON body. */
export async function analyze(
  request: AnalyzeRequest,
  signal?: AbortSignal,
  onSource?: (fips: string, status: SourceStatus) => void,
): Promise<AnalyzeResponse> {
  const params = new URLSearchParams();
  if (FIXTURE_MODE) params.set("fixture", "1");
  if (onSource) params.set("progress", "1");
  const query = params.toString();
  const resp = await fetch(`/api/analyze${query ? `?${query}` : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!resp.ok) throw new Error(await readError(resp));

  if (!onSource) {
    return (await resp.json()) as AnalyzeResponse;
  }
  if (!resp.body) throw new Error("The server sent no stream.");

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: AnalyzeResponse | null = null;

  const handle = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const event = JSON.parse(trimmed) as AnalyzeEvent;
    if (event.type === "source" && event.fips && event.status) {
      onSource(event.fips, event.status);
    } else if (event.type === "done" && event.response) {
      result = event.response;
    } else if (event.type === "error") {
      throw new Error(event.message ?? "The analysis failed.");
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      handle(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
    }
  }
  handle(buffer);
  if (!result) throw new Error("The server ended the stream without a report.");
  return result;
}

/** Run Site Scout for one opportunity in a county. The response is NDJSON,
 * one event per line; `onEvent` is called for each as it arrives, the last
 * being the result. */
export async function scout(
  fips: string,
  needIds: NeedId[],
  focus: string,
  solutionId: string | null,
  assetIds: string[],
  detail: string | null,
  onEvent: (event: ScoutEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`/api/scout${FIXTURE_MODE ? "?fixture=1" : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fips,
      need_ids: needIds,
      focus,
      solution_id: solutionId,
      asset_ids: assetIds,
      detail,
    }),
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

/** The checklist and document pack for one opportunity and its chosen site. */
export async function fetchProcess(request: ProcessRequest): Promise<ProcessPlan> {
  const resp = await fetch("/api/process", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as ProcessPlan;
}
