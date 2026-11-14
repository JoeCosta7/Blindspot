"""EPA ECHO drinking water (SDWA) web services: active community water
systems serving the county, with their violation flags. Free, no key.
Cached for 7 days.

Two calls, as ECHO's services work: `get_systems` runs the query and returns
a QueryID, then `get_qid` pages through the rows. Parameters `p_fips` (5-digit
county FIPS), `p_act=Y` (active) and `p_pswt=CWS` (community water systems,
the ones serving homes year-round) were confirmed with live calls on
2026-11-14; ECHO's web-services page does not list them.

Field meanings, from https://echo.epa.gov/help/facility-search/drinking-water-search-results-help
(checked 2026-11-14):
- HealthFlag: "Violations of maximum contaminant levels (MCLs) or maximum
  residual disinfectant levels (MRDLs)".
- SeriousViolator, shown as "Enforcement Priority": "a public water system
  with unresolved serious, multiple, and/or continuing violations".
- QtrsWithVio: quarters in violation over the past three years.
- SDWAContaminantsInViol3yr: contaminants or rules in violation in the past
  three years.
- PbAle: lead action level exceedances in the past five years.
Violation data lag: a quarter's data reach SDWIS after the following quarter.

PopulationServedCount is the system's whole population, which can include
people in neighbouring counties the system also serves.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

BASE = "https://echodata.epa.gov/echo/sdw_rest_services"
SOURCE_LABEL = "EPA ECHO drinking water data (SDWIS)"
PAGE_SIZE = 500


def _int(value: Any) -> int:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0


def parse_system(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("PWSId"),
        "name": str(row.get("PWSName") or "Unnamed water system").strip(),
        "population": _int(row.get("PopulationServedCount")),
        "health_violation": row.get("HealthFlag") == "Yes",
        "enforcement_priority": row.get("SeriousViolator") == "Yes",
        "quarters_with_violation": _int(row.get("QtrsWithVio")),
        "lead_exceedances": _int(row.get("PbAle")),
        "contaminants_3yr": row.get("SDWAContaminantsInViol3yr"),
        "source_water": row.get("PrimarySourceDesc"),
        "owner": row.get("OwnerDesc"),
    }


async def _get(client: httpx.AsyncClient, settings: Settings, service: str, params: dict) -> dict:
    try:
        resp = await client.get(
            f"{BASE}.{service}",
            params={"output": "JSON", **params},
            timeout=settings.source_timeout_s,
        )
    except httpx.HTTPError as exc:
        raise SourceError(f"ECHO request failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise SourceError(f"ECHO returned HTTP {resp.status_code}")
    try:
        results = resp.json()["Results"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("ECHO response had no Results") from exc
    if "Error" in results:
        raise SourceError(f"ECHO error: {results['Error']}")
    return results


async def fetch_water_systems(
    client: httpx.AsyncClient, settings: Settings, fips: str
) -> tuple[list[dict[str, Any]], bool]:
    async def fetch() -> list[dict[str, Any]]:
        query = await _get(
            client,
            settings,
            "get_systems",
            {"p_fips": fips, "p_act": "Y", "p_pswt": "CWS", "responseset": str(PAGE_SIZE)},
        )
        total = _int(query.get("QueryRows"))
        qid = query.get("QueryID")
        if total == 0:
            return []
        if not qid:
            raise SourceError("ECHO returned no QueryID")
        systems: list[dict[str, Any]] = []
        page = 1
        while len(systems) < total:
            results = await _get(
                client, settings, "get_qid", {"qid": qid, "pageno": str(page)}
            )
            rows = results.get("WaterSystems") or []
            if not rows:
                break
            systems += [parse_system(r) for r in rows]
            page += 1
        return systems

    return await cached_fetch(settings.cache_dir, "echo_water", {"fips": fips}, 7 * DAY, fetch)
