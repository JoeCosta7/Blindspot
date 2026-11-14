"""FastAPI app: health, county boundaries and the analyze endpoint."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app import matching, process
from app.config import Settings, get_settings
from app.geo import County, CountyIndex
from app.models import (
    AnalyzeBboxRequest,
    AnalyzeRequest,
    AnalyzeResponse,
    CountyReport,
    HealthResponse,
    NeedMapResponse,
    ProcessPlan,
    ProcessRequest,
    ScoutEvent,
    ScoutRequest,
    ScoutResult,
    ScoutStep,
)
from app.needmap import build_need_map
from app.pipeline import analyze_county
from app.scout import agent as scout_agent
from app.scout import provider as scout_provider
from app.sources import SourceError, census, energy_communities, vexcel

log = logging.getLogger(__name__)

DATA_FILES = (
    "counties.geojson",
    "repowering.parquet",
    "lmop.parquet",
    "nri_counties.parquet",
    "svi_counties.parquet",
    "state_energy.json",
    "dams.parquet",
    "farms.parquet",
)


def load_county_index(settings: Settings) -> CountyIndex:
    path = settings.data_dir / "counties.geojson"
    if not path.exists():
        log.warning("%s is missing; run scripts/prep_data.py", path)
        return CountyIndex([])
    return CountyIndex.from_geojson(path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    app.state.counties = load_county_index(settings)
    async with httpx.AsyncClient(follow_redirects=False) as client:
        app.state.client = client
        yield


app = FastAPI(title="County Sustainability Planner", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _fixture_path(settings: Settings, fips: str) -> Path:
    return settings.data_dir / "fixtures" / f"{fips}.json"


def _load_fixture(settings: Settings, county: County) -> CountyReport | None:
    path = _fixture_path(settings, county.fips)
    if not path.exists():
        return None
    return CountyReport.model_validate_json(path.read_text())


@app.get("/api/health")
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(
        ok=True,
        counties_loaded=len(request.app.state.counties),
        data_files={name: (settings.data_dir / name).exists() for name in DATA_FILES},
        keys={
            "census": bool(settings.census_api_key),
            "nlr": bool(settings.nlr_api_key),
            "gemini": bool(settings.gemini_api_key),
        },
    )


@app.get("/api/counties.geojson")
async def counties_geojson(request: Request) -> FileResponse:
    path = request.app.state.settings.data_dir / "counties.geojson"
    if not path.exists():
        raise HTTPException(503, "County boundaries are missing; run scripts/prep_data.py")
    return FileResponse(
        path, media_type="application/geo+json", headers={"Cache-Control": "max-age=86400"}
    )


@app.get("/api/need-map")
async def need_map(request: Request) -> NeedMapResponse:
    """Every Census need for every county, for coloring the map. Two Census
    calls, both cached for 30 days and shared with the reports."""
    settings: Settings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.client
    try:
        (all_values, _), (national, _) = await asyncio.gather(
            census.fetch_all_counties(client, settings),
            census.fetch_national(client, settings),
        )
    except SourceError as exc:
        raise HTTPException(503, f"County figures could not be loaded: {exc}") from exc
    return build_need_map(all_values, national, settings)


@app.post("/api/analyze")
async def analyze(
    body: AnalyzeRequest,
    request: Request,
    fixture: bool = Query(False, description="Serve saved reports; no network calls"),
) -> AnalyzeResponse:
    settings: Settings = request.app.state.settings
    index: CountyIndex = request.app.state.counties

    truncated = False
    if isinstance(body, AnalyzeBboxRequest):
        counties, truncated = index.resolve_bbox(
            body.sw, body.ne, settings.max_counties, settings.bbox_min_area_share
        )
    else:
        county = index.get(body.fips)
        if county is None:
            raise HTTPException(404, f"No county with FIPS {body.fips}")
        counties = [county]

    if fixture:
        loaded = [_load_fixture(settings, c) for c in counties]
        reports = [r for r in loaded if r is not None]
        skipped = len(loaded) - len(reports)
        if not reports and counties:
            raise HTTPException(404, "No saved report for this selection")
        message = f"{skipped} selected counties have no saved report." if skipped else None
    else:
        reports = list(
            await asyncio.gather(
                *(analyze_county(c, request.app.state.client, settings) for c in counties)
            )
        )
        message = None

    if truncated:
        message = (
            f"The box covers more than {settings.max_counties} counties. "
            f"Showing the {settings.max_counties} with the largest overlap; "
            "zoom in to choose others."
        )
    elif not counties:
        message = "No county matched the box. Draw it over a county's center or most of its area."

    return AnalyzeResponse(counties=reports, truncated=truncated, message=message)


# --- Site Scout -----------------------------------------------------------------------

_IMAGE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _line(event: ScoutEvent) -> str:
    return event.model_dump_json() + "\n"


async def _replay_fixture(saved: ScoutResult, delay_s: float) -> AsyncIterator[str]:
    """Play a saved run back step by step, so the demo looks live offline."""
    shown: set[str] = set()
    for step in saved.steps:
        yield _line(ScoutEvent(type="step", step=step))
        if step.kind == "decision" and step.building_id and step.building_id not in shown:
            candidate = next(
                (c for c in saved.candidates if c.building_id == step.building_id), None
            )
            if candidate:
                shown.add(step.building_id)
                yield _line(ScoutEvent(type="candidate", candidate=candidate))
        await asyncio.sleep(delay_s)
    replayed = saved.model_copy(update={"generated_by": "fixture"})
    yield _line(ScoutEvent(type="result", result=replayed))


@app.post("/api/process")
async def process_plan(body: ProcessRequest, request: Request) -> ProcessPlan:
    """The checklist and document pack for one opportunity and its chosen site.
    Built from what the client sends; nothing is re-analyzed."""
    return await process.build_process(body, request.app.state.settings)


@app.post("/api/scout")
async def scout(
    body: ScoutRequest,
    request: Request,
    fixture: bool = Query(False, description="Replay a saved run; no network calls"),
) -> StreamingResponse:
    """Run the building scout for one county. The response is NDJSON: one
    ScoutEvent per line, steps and candidates as they happen, a result last."""
    settings: Settings = request.app.state.settings
    index: CountyIndex = request.app.state.counties
    county = index.get(body.fips)
    if county is None:
        raise HTTPException(404, f"No county with FIPS {body.fips}")
    if body.fips not in settings.scout_counties:
        raise HTTPException(404, "Site Scout has building data only for the counties it covers")

    if fixture:
        path = settings.data_dir / "fixtures" / f"scout_{body.fips}.json"
        if not path.exists():
            raise HTTPException(404, "No saved scout run for this county")
        saved = ScoutResult.model_validate_json(path.read_text())
        return StreamingResponse(
            _replay_fixture(saved, settings.scout_fixture_step_delay_s),
            media_type="application/x-ndjson",
        )

    client: httpx.AsyncClient = request.app.state.client

    async def events() -> AsyncIterator[str]:
        yield _line(
            ScoutEvent(
                type="step",
                step=ScoutStep(index=0, kind="plan", text="Loading the county report."),
            )
        )
        report = await analyze_county(county, client, settings)
        try:
            energy_community = energy_communities.status_for_county(settings.data_dir, county.fips)
            checked = True
        except Exception:  # the parquet file is optional here
            energy_community, checked = None, False
        provider = scout_provider.get_provider(client, settings)
        profile = scout_agent.profile_for(body.solution_id, body.focus)
        own = [a for a in report.assets if a.id in body.asset_ids]
        report_sites = own + [
            a for a in report.assets if a.type in profile.site_types and a.id not in body.asset_ids
        ]
        report_sites = [a for a in report_sites if a.type in profile.site_types] or own
        # The two EPA lists name the same landfill differently; keep the first.
        unique: list = []
        for a in report_sites:
            same = any(
                a.name.lower() == u.name.lower()
                or (abs(a.lat - u.lat) < 0.003 and abs(a.lon - u.lon) < 0.003)
                for u in unique
            )
            if not same:
                unique.append(a)
        report_sites = unique
        site_solution = next(
            (
                s
                for s in matching.load_solutions(settings.data_dir / "solutions.json")
                if s.id == body.solution_id
            ),
            None,
        )
        needs = report.needs
        if body.need_ids:
            needs = [n for n in needs if n.id in body.need_ids] or needs
        async for event in scout_agent.run_scout(
            county,
            needs,
            report.resources.solar_kwh_per_kw_year,
            provider,
            settings,
            client,
            energy_community=energy_community,
            energy_community_checked=checked,
            focus=body.focus,
            profile=profile,
            report_sites=report_sites,
            site_solution=site_solution,
            public_buildings=[
                a for a in report.assets if a.type in ("school", "library", "college")
            ],
        ):
            if event.step is not None:
                event = event.model_copy(
                    update={"step": event.step.model_copy(update={"index": event.step.index + 1})}
                )
            yield _line(event)

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.get("/api/scout/image/{name}")
async def scout_image(name: str, request: Request) -> FileResponse:
    """An aerial image the scout fetched and cached, with outlines drawn."""
    settings: Settings = request.app.state.settings
    if not _IMAGE_NAME.match(name):
        raise HTTPException(404, "No such image")
    path = vexcel.find_image(settings, name)
    if path is None:
        raise HTTPException(404, "No such image")
    media = "image/jpeg" if path.suffix == ".jpg" else "image/png"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=86400"})


@app.get("/api/scout/oblique/{name}/{direction}")
async def scout_oblique(name: str, direction: str, request: Request) -> FileResponse:
    """A side-angle photo of a scouted site from one direction, fetched from
    Vexcel on first request and cached after."""
    settings: Settings = request.app.state.settings
    if not _IMAGE_NAME.match(name) or direction not in vexcel.OBLIQUE_DIRECTIONS:
        raise HTTPException(404, "No such image")
    job = vexcel.load_image_job(settings, name)
    if job is None or direction not in job.get("directions", []):
        raise HTTPException(404, "No such image")
    try:
        path = await vexcel.oblique_jpg(
            request.app.state.client,
            settings,
            job["area"],
            direction,
            job["drawings"],
            f"{name}_{direction}",
        )
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
