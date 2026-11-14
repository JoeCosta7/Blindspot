"""BTS National Transit Map stops (NTAD), from its ArcGIS feature service.
Free, no key. Cached for 30 days.

The map is compiled from agencies' GTFS schedule feeds, so it covers
fixed-route service only: demand-response and dial-a-ride service, common in
rural counties, is not in it, and an agency that publishes no feed is
missing. Stops are counted inside the simplified county outline and grouped
by `ntd_id` (the agency's National Transit Database id). Service URL and
fields confirmed on 2026-11-14 (layer description: compiled 2026-09-23).
"""

from __future__ import annotations

from typing import Any

import httpx
from shapely.geometry.base import BaseGeometry

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import _arcgis

URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "NTAD_National_Transit_Map_Stops/FeatureServer/0/query"
)
SOURCE_LABEL = "BTS National Transit Map (stops)"


def summarize(counts: dict[str, int]) -> dict[str, Any]:
    agencies = [key for key in counts if key not in ("None", "")]
    return {"stops": sum(counts.values()), "agencies": len(agencies)}


async def fetch_stops(
    client: httpx.AsyncClient, settings: Settings, fips: str, geometry: BaseGeometry
) -> tuple[dict[str, Any], bool]:
    async def fetch() -> dict[str, Any]:
        body = await _arcgis.query(
            client,
            URL,
            {**_arcgis.count_by("ntd_id"), **_arcgis.within(geometry)},
            settings.source_timeout_s,
            "Transit map",
        )
        return summarize(_arcgis.grouped_counts(body, "ntd_id"))

    return await cached_fetch(settings.cache_dir, "transit_stops", {"fips": fips}, 30 * DAY, fetch)
