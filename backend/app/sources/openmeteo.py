"""Open-Meteo historical weather: a ten-year climate summary at one point.

Open-Meteo serves reanalysis data (ERA5 and related models, a grid of about
10-25 km), not station records. The free API needs no key and is for
non-commercial use; data is CC BY 4.0 and the UI must credit Open-Meteo.
https://open-meteo.com/en/docs/historical-weather-api

Parameters and response fields (`daily.temperature_2m_mean`, `daily_units`,
`hourly.wind_speed_100m` with `wind_speed_unit=ms`) were confirmed with live
calls on 2026-11-14.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE_LABEL = "Open-Meteo historical weather (ERA5 reanalysis)"

FIRST_YEAR = 2016
LAST_YEAR = 2025  # the last full year
WIND_YEAR = 2025  # hourly data is large, so wind uses one year

DAILY = ("temperature_2m_mean", "temperature_2m_max", "precipitation_sum", "snowfall_sum")
# US convention: degree days against 65 F, hot days at 90 F or more.
DEGREE_DAY_BASE_F = 65.0
HOT_DAY_F = 90.0
DAYS_PER_YEAR = 365.25


def _c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def _per_year(total: float, valid_days: int) -> float:
    return total / valid_days * DAYS_PER_YEAR


def summarize(daily: dict[str, list[Any]], wind_100m: list[Any] | None) -> dict[str, float]:
    """Yearly averages from daily series (deg C, mm, cm) and hourly 100 m wind
    (m/s). Missing days are skipped and totals scaled to a full year. A metric
    with no data is left out."""
    out: dict[str, float] = {}

    means = [v for v in daily.get("temperature_2m_mean", []) if v is not None]
    if means:
        temps_f = [_c_to_f(v) for v in means]
        out["heating_degree_days"] = _per_year(
            sum(max(0.0, DEGREE_DAY_BASE_F - t) for t in temps_f), len(temps_f)
        )
        out["cooling_degree_days"] = _per_year(
            sum(max(0.0, t - DEGREE_DAY_BASE_F) for t in temps_f), len(temps_f)
        )

    highs = [v for v in daily.get("temperature_2m_max", []) if v is not None]
    if highs:
        hot = sum(1 for v in highs if _c_to_f(v) >= HOT_DAY_F)
        out["hot_days"] = _per_year(hot, len(highs))

    rain = [v for v in daily.get("precipitation_sum", []) if v is not None]
    if rain:
        out["precipitation_in"] = _per_year(sum(rain) / 25.4, len(rain))

    snow = [v for v in daily.get("snowfall_sum", []) if v is not None]
    if snow:
        out["snowfall_in"] = _per_year(sum(snow) / 2.54, len(snow))

    wind = [v for v in (wind_100m or []) if v is not None]
    if wind:
        out["wind_speed_100m"] = sum(wind) / len(wind)
    return out


async def _get(client: httpx.AsyncClient, settings: Settings, params: dict[str, Any]) -> Any:
    try:
        resp = await client.get(URL, params=params, timeout=settings.source_timeout_s)
    except httpx.HTTPError as exc:
        raise SourceError(f"Open-Meteo request failed: {type(exc).__name__}") from exc
    if resp.status_code == 429:
        raise SourceError("Open-Meteo rate limit reached")
    if resp.status_code != 200:
        reason = ""
        try:
            reason = f": {resp.json().get('reason', '')}"[:200]
        except ValueError:
            pass
        raise SourceError(f"Open-Meteo returned HTTP {resp.status_code}{reason}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError("Open-Meteo returned a response that is not JSON") from exc


async def fetch_climate(
    client: httpx.AsyncClient, settings: Settings, lat: float, lon: float
) -> tuple[dict[str, float], bool]:
    """(climate summary, came_from_cache). Cached by coordinates rounded to
    two decimals; the summary changes only when the years do."""
    lat_r, lon_r = round(lat, 2), round(lon, 2)
    point = {"latitude": lat_r, "longitude": lon_r, "timezone": "auto"}

    async def fetch() -> dict[str, float]:
        daily_body, wind_body = await asyncio.gather(
            _get(
                client,
                settings,
                {
                    **point,
                    "start_date": f"{FIRST_YEAR}-01-01",
                    "end_date": f"{LAST_YEAR}-12-31",
                    "daily": ",".join(DAILY),
                },
            ),
            _get(
                client,
                settings,
                {
                    **point,
                    "start_date": f"{WIND_YEAR}-01-01",
                    "end_date": f"{WIND_YEAR}-12-31",
                    "hourly": "wind_speed_100m",
                    "wind_speed_unit": "ms",
                },
            ),
        )
        daily = daily_body.get("daily")
        if not isinstance(daily, dict):
            raise SourceError("Open-Meteo response had no daily data")
        wind = (wind_body.get("hourly") or {}).get("wind_speed_100m")
        summary = summarize(daily, wind)
        if not summary:
            raise SourceError("Open-Meteo returned no usable values for this point")
        return summary

    value, cached = await cached_fetch(
        settings.cache_dir,
        "openmeteo_climate",
        {"lat": lat_r, "lon": lon_r, "years": [FIRST_YEAR, LAST_YEAR, WIND_YEAR]},
        90 * DAY,
        fetch,
    )
    return dict(value), cached
