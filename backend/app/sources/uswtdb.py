"""US Wind Turbine Database (USGS, LBNL, ACP), through its public API. Free,
no key. Cached for 30 days.

`GET /api/uswtdb/v1/turbines?t_fips=eq.<county FIPS>` (a PostgREST filter)
returns one row per turbine; `t_cap` is turbine capacity in kW, `p_name` the
project and `p_year` its online year. Confirmed with live calls on
2026-11-14 (Kern County, CA returned all 3,250 turbines in one response).
https://energy.usgs.gov/uswtdb/api-doc/
"""

from __future__ import annotations

from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

URL = "https://energy.usgs.gov/api/uswtdb/v1/turbines"
SOURCE_LABEL = "US Wind Turbine Database (USGS)"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    capacities = [float(r["t_cap"]) for r in rows if isinstance(r.get("t_cap"), (int, float))]
    years = [int(r["p_year"]) for r in rows if isinstance(r.get("p_year"), int)]
    return {
        "turbines": len(rows),
        "with_capacity": len(capacities),
        "capacity_mw": round(sum(capacities) / 1000, 1),
        "projects": len({r.get("p_name") for r in rows if r.get("p_name")}),
        "offshore": sum(1 for r in rows if r.get("t_offshore") == 1),
        "first_year": min(years) if years else None,
        "latest_year": max(years) if years else None,
    }


async def fetch_turbines(
    client: httpx.AsyncClient, settings: Settings, fips: str
) -> tuple[dict[str, Any], bool]:
    async def fetch() -> dict[str, Any]:
        try:
            resp = await client.get(
                URL,
                params={"t_fips": f"eq.{fips}", "select": "p_name,p_year,t_cap,t_offshore"},
                timeout=settings.source_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"Wind turbine API request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise SourceError(f"Wind turbine API returned HTTP {resp.status_code}")
        try:
            rows = resp.json()
        except ValueError as exc:
            raise SourceError("Wind turbine API returned a response that is not JSON") from exc
        if not isinstance(rows, list):
            raise SourceError("Wind turbine API returned an unexpected response")
        return summarize(rows)

    return await cached_fetch(settings.cache_dir, "uswtdb", {"fips": fips}, 30 * DAY, fetch)
