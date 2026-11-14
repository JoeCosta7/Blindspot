"""NLR PVWatts v8: annual AC output of a 1 kW fixed-tilt system at a point,
which is the yield in kWh per kW per year.

Use developer.nlr.gov only: developer.nrel.gov was retired on 2026-05-29.
Parameters and the `outputs.ac_annual` field were confirmed with a live call
on 2026-11-13.
"""

from __future__ import annotations

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

URL = "https://developer.nlr.gov/api/pvwatts/v8.json"

# These fixed inputs are shown to the user as assumptions (see estimates.py).
TILT_DEGREES = 20
LOSSES_PERCENT = 14


async def fetch_yield(
    client: httpx.AsyncClient, settings: Settings, lat: float, lon: float
) -> tuple[float, bool]:
    """(kWh per kW per year, came_from_cache). Cached by coordinates rounded to
    two decimals, about 1 km."""
    lat_r, lon_r = round(lat, 2), round(lon, 2)

    async def fetch() -> float:
        if not settings.nlr_api_key:
            raise SourceError("NLR_API_KEY is not set")
        try:
            resp = await client.get(
                URL,
                params={
                    "api_key": settings.nlr_api_key,
                    "lat": lat_r,
                    "lon": lon_r,
                    "system_capacity": 1,
                    "module_type": 0,
                    "losses": LOSSES_PERCENT,
                    "array_type": 0,  # fixed, open rack
                    "tilt": TILT_DEGREES,
                    "azimuth": 180,
                },
                timeout=settings.source_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"PVWatts request failed: {type(exc).__name__}") from exc
        if resp.status_code == 429:
            raise SourceError("PVWatts rate limit reached")
        if resp.status_code != 200:
            raise SourceError(f"PVWatts returned HTTP {resp.status_code}")
        body = resp.json()
        if body.get("errors"):
            raise SourceError(f"PVWatts error: {'; '.join(map(str, body['errors']))[:200]}")
        try:
            return float(body["outputs"]["ac_annual"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError("PVWatts response had no outputs.ac_annual") from exc

    value, cached = await cached_fetch(
        settings.cache_dir, "pvwatts", {"lat": lat_r, "lon": lon_r}, 90 * DAY, fetch
    )
    return float(value), cached
