"""FastAPI app: health, county boundaries and the analyze endpoint."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse

from app.config import Settings, get_settings
from app.geo import County, CountyIndex
from app.models import (
    AnalyzeBboxRequest,
    AnalyzeRequest,
    AnalyzeResponse,
    CountyReport,
    HealthResponse,
)
from app.pipeline import analyze_county

log = logging.getLogger(__name__)

DATA_FILES = ("counties.geojson", "repowering.parquet", "lmop.parquet", "nri_counties.parquet")


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
