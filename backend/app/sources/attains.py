"""EPA ATTAINS assessed waters, from EPA's ATTAINS_Assessment map service:
state water quality decisions reported under Clean Water Act sections 303(d)
and 305(b). Free, no key (the attains.epa.gov REST API now needs an
api.data.gov key; this service does not). County results cached 7 days, the
national baseline 30 days.

Layers 0 (points), 1 (lines: streams and rivers) and 2 (areas: lakes,
estuaries) are queried for assessment units that intersect the county.
`overallstatus` is "Fully Supporting", "Not Supporting" or "Not Assessed";
a unit is impaired when it is "Not Supporting" (the service's `isimpaired`
flag agreed on every unit checked). Cause columns hold "Cause" when that
pollutant is a cause of impairment. Field names and values were read from
the layer definitions and live queries on 2026-11-14.
https://gispub.epa.gov/arcgis/rest/services/OW/ATTAINS_Assessment/MapServer

States split waters into assessment units and choose what to assess in very
different ways, so counts are not comparable across state lines. A unit that
crosses the county line counts in full.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import httpx
from shapely.geometry.base import BaseGeometry

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import _arcgis

URL = "https://gispub.epa.gov/arcgis/rest/services/OW/ATTAINS_Assessment/MapServer/{layer}/query"
SOURCE_LABEL = "EPA ATTAINS assessed waters"
LAYERS = (0, 1, 2)
IMPAIRED = "Not Supporting"
SUPPORTING = "Fully Supporting"
MAX_IMPAIRED_ROWS = 2000

# Cause columns in the service -> plain names. A subset of the service's
# cause fields: the ones a county program could act on.
CAUSES = {
    "pathogens": "bacteria and other pathogens",
    "nutrients": "nutrients",
    "sediment": "sediment",
    "oxygen_depletion": "low oxygen",
    "temperature": "temperature",
    "mercury": "mercury",
    "metals_other_than_mercury": "metals",
    "ph_acidity_caustic_conditions": "acidity (pH)",
    "polychlorinated_biphenyls_pcbs": "PCBs",
    "pfas": "PFAS",
    "algal_growth": "algae",
    "solids_chlorides_sulfates": "salts and dissolved solids",
    "turbidity": "turbidity",
    "habitat_alterations": "habitat damage",
    "trash": "trash",
    "cause_unknown_impaired_biota": "harmed aquatic life, cause unknown",
}


def summarize(status_counts: dict[str, int], impaired_rows: list[dict[str, Any]]) -> dict:
    causes = Counter(
        label
        for row in impaired_rows
        for field, label in CAUSES.items()
        if row.get(field) == "Cause"
    )
    names = sorted(
        {str(r["assessmentunitname"]).strip() for r in impaired_rows if r.get("assessmentunitname")}
    )
    return {
        "assessed": status_counts.get(SUPPORTING, 0) + status_counts.get(IMPAIRED, 0),
        "impaired": status_counts.get(IMPAIRED, 0),
        "not_assessed": status_counts.get("Not Assessed", 0),
        "causes": dict(causes.most_common()),
        "impaired_names": names[:10],
    }


async def _status_counts(
    client: httpx.AsyncClient, settings: Settings, geometry_params: dict[str, str]
) -> dict[str, int]:
    bodies = await asyncio.gather(
        *(
            _arcgis.query(
                client,
                URL.format(layer=layer),
                {**_arcgis.count_by("overallstatus"), **geometry_params},
                settings.source_timeout_s,
                "ATTAINS",
            )
            for layer in LAYERS
        )
    )
    total: Counter[str] = Counter()
    for body in bodies:
        total.update(_arcgis.grouped_counts(body, "overallstatus"))
    return dict(total)


async def fetch_national(client: httpx.AsyncClient, settings: Settings) -> tuple[dict, bool]:
    async def fetch() -> dict:
        counts = await _status_counts(client, settings, {})
        return summarize(counts, [])

    return await cached_fetch(settings.cache_dir, "attains_national", {}, 30 * DAY, fetch)


async def fetch_county_waters(
    client: httpx.AsyncClient, settings: Settings, fips: str, geometry: BaseGeometry
) -> tuple[dict[str, Any], bool]:
    """{"county": summary, "national": summary}."""

    async def fetch() -> dict[str, Any]:
        area = _arcgis.within(geometry)
        listing = {
            **area,
            "where": f"overallstatus='{IMPAIRED}'",
            "outFields": ",".join(["assessmentunitname", *CAUSES]),
            "returnGeometry": "false",
            "resultRecordCount": str(MAX_IMPAIRED_ROWS),
        }
        counts, *impaired = await asyncio.gather(
            _status_counts(client, settings, area),
            *(
                _arcgis.query(
                    client, URL.format(layer=layer), listing, settings.source_timeout_s, "ATTAINS"
                )
                for layer in LAYERS
            ),
        )
        rows = [f.get("attributes", {}) for body in impaired for f in body.get("features", [])]
        return summarize(counts, rows)

    county, cached = await cached_fetch(
        settings.cache_dir, "attains_county", {"fips": fips}, 7 * DAY, fetch
    )
    national, _ = await fetch_national(client, settings)
    return {"county": county, "national": national}, cached
