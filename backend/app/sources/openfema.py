"""OpenFEMA Disaster Declarations Summaries v2: major disaster declarations
that named the county. Free, no key. Cached for 7 days.

Parameters ($filter on fipsStateCode, fipsCountyCode, declarationType and
declarationDate; $select; $top; $orderby) and fields (disasterNumber,
declarationDate, incidentType, declarationTitle) were confirmed with live
calls on 2026-11-14. https://www.fema.gov/about/openfema/disaster-declarations-summaries

Only "DR" (major disaster) declarations are counted, not emergency ("EM") or
fire management ("FM") declarations. A statewide declaration that names no
county is not counted. Tribal declarations are filed under the state.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
SOURCE_LABEL = "OpenFEMA disaster declarations"


def summarize(rows: list[dict[str, Any]], since_year: int) -> dict[str, Any]:
    """One entry per disaster: a county can appear on several rows of one."""
    disasters: dict[int, dict[str, Any]] = {}
    for row in rows:
        number = row.get("disasterNumber")
        if number is not None:
            disasters.setdefault(int(number), row)
    by_type = Counter(str(r.get("incidentType") or "Other") for r in disasters.values())
    latest = max(disasters.values(), key=lambda r: str(r.get("declarationDate")), default=None)
    return {
        "since_year": since_year,
        "count": len(disasters),
        "by_type": dict(by_type.most_common()),
        "latest_title": str(latest["declarationTitle"]).strip() if latest else None,
        "latest_date": str(latest["declarationDate"])[:10] if latest else None,
    }


async def fetch_declarations(
    client: httpx.AsyncClient, settings: Settings, state_fips: str, county_code: str
) -> tuple[dict[str, Any], bool]:
    since_year = dt.date.today().year - settings.disaster_history_years

    async def fetch() -> dict[str, Any]:
        params = {
            "$filter": (
                f"fipsStateCode eq '{state_fips}' and fipsCountyCode eq '{county_code}' "
                f"and declarationType eq 'DR' "
                f"and declarationDate ge '{since_year}-01-01T00:00:00.000Z'"
            ),
            "$select": "disasterNumber,declarationDate,incidentType,declarationTitle",
            "$orderby": "declarationDate desc",
            "$top": "1000",
        }
        try:
            resp = await client.get(URL, params=params, timeout=settings.source_timeout_s)
        except httpx.HTTPError as exc:
            raise SourceError(f"OpenFEMA request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise SourceError(f"OpenFEMA returned HTTP {resp.status_code}")
        try:
            rows = resp.json()["DisasterDeclarationsSummaries"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError("OpenFEMA response had no DisasterDeclarationsSummaries") from exc
        return summarize(rows, since_year)

    return await cached_fetch(
        settings.cache_dir,
        "openfema",
        {"state": state_fips, "county": county_code, "since": since_year},
        7 * DAY,
        fetch,
    )
