"""NLR alternative fuel station API: public, open EV charging stations.

One call per state, cached for 24 hours; the pipeline then filters by county
polygon. Parameters (`fuel_type`, `state`, `status`, `access`, `country`,
`limit`) and the `total_results` and `fuel_stations` fields were confirmed with
live calls on 2026-11-13.

Known limit: the largest states return tens of megabytes and can exceed the
15 s source timeout on a cold cache. The request still succeeds without them.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

URL = "https://developer.nlr.gov/api/alt-fuel-stations/v1.json"
SOURCE_LABEL = "NLR Alternative Fuel Stations"

_FILTERS = {"fuel_type": "ELEC", "status": "E", "access": "public"}


async def _get(client: httpx.AsyncClient, settings: Settings, params: dict[str, Any]) -> dict:
    if not settings.nlr_api_key:
        raise SourceError("NLR_API_KEY is not set")
    try:
        resp = await client.get(
            URL,
            params={"api_key": settings.nlr_api_key, **_FILTERS, **params},
            timeout=settings.source_timeout_s,
        )
    except httpx.HTTPError as exc:
        raise SourceError(f"Station API request failed: {type(exc).__name__}") from exc
    if resp.status_code == 429:
        raise SourceError("Station API rate limit reached")
    if resp.status_code != 200:
        raise SourceError(f"Station API returned HTTP {resp.status_code}")
    return resp.json()


async def fetch_state_stations(
    client: httpx.AsyncClient, settings: Settings, state: str
) -> tuple[list[dict[str, Any]], bool]:
    """Stations in a state, trimmed to the fields we use so the cache stays
    small: id, name, city, lat, lon, level2_ports, dc_fast_ports."""

    async def fetch() -> list[dict[str, Any]]:
        body = await _get(client, settings, {"state": state})
        stations = []
        for s in body.get("fuel_stations", []):
            if s.get("latitude") is None or s.get("longitude") is None:
                continue
            stations.append(
                {
                    "id": s.get("id"),
                    "name": s.get("station_name") or "EV charging station",
                    "city": s.get("city"),
                    "lat": float(s["latitude"]),
                    "lon": float(s["longitude"]),
                    "level2_ports": s.get("ev_level2_evse_num"),
                    "dc_fast_ports": s.get("ev_dc_fast_num"),
                }
            )
        return stations

    value, cached = await cached_fetch(
        settings.cache_dir, "afdc_state", {"state": state}, DAY, fetch
    )
    return value, cached


async def fetch_national_count(client: httpx.AsyncClient, settings: Settings) -> tuple[int, bool]:
    """Number of public, open EV stations in the US, for the benchmark."""

    async def fetch() -> int:
        body = await _get(client, settings, {"country": "US", "limit": 1})
        try:
            return int(body["total_results"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError("Station API response had no total_results") from exc

    value, cached = await cached_fetch(
        settings.cache_dir, "afdc_national_count", {}, 7 * DAY, fetch
    )
    return int(value), cached
