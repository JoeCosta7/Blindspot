"""OpenStreetMap via Overpass: schools, libraries and colleges in a bounding
box. The pipeline filters the result by county polygon.

Overpass is a shared free service: one request per county, cached 7 days,
never in a loop. It answers 406 to requests without a User-Agent (seen
2026-11-13), so one is always sent.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

USER_AGENT = "county-sustainability-planner/0.1 (hackathon project)"
SOURCE_LABEL = "OpenStreetMap"

# OSM amenity value -> our asset type
AMENITY_TYPES = {
    "school": "school",
    "library": "library",
    "college": "college",
    "university": "college",
}


def build_query(south: float, west: float, north: float, east: float, timeout_s: int) -> str:
    amenities = "|".join(AMENITY_TYPES)
    return (
        f"[out:json][timeout:{timeout_s}];"
        f'nwr["amenity"~"^({amenities})$"]({south},{west},{north},{east});'
        "out center tags;"
    )


def parse_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Named buildings with a location. Nodes carry lat/lon; ways and relations
    carry a `center`. Unnamed features are dropped: they cannot be shown to an
    official as a place."""
    buildings = []
    for el in elements:
        tags = el.get("tags") or {}
        asset_type = AMENITY_TYPES.get(tags.get("amenity", ""))
        name = (tags.get("name") or "").strip()
        point = el if "lat" in el else el.get("center") or {}
        if not asset_type or not name or "lat" not in point or "lon" not in point:
            continue
        buildings.append(
            {
                "osm_id": f"{el.get('type')}/{el.get('id')}",
                "type": asset_type,
                "name": name,
                "lat": float(point["lat"]),
                "lon": float(point["lon"]),
                "city": tags.get("addr:city"),
            }
        )
    return buildings


async def fetch_public_buildings(
    client: httpx.AsyncClient,
    settings: Settings,
    fips: str,
    bounds: tuple[float, float, float, float],
) -> tuple[list[dict[str, Any]], bool]:
    """`bounds` is (west, south, east, north). Cached per county."""
    west, south, east, north = bounds

    async def fetch() -> list[dict[str, Any]]:
        query = build_query(south, west, north, east, int(settings.source_timeout_s))
        try:
            resp = await client.post(
                settings.overpass_url,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=settings.source_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"Overpass request failed: {type(exc).__name__}") from exc
        if resp.status_code in (429, 502, 503, 504):  # 502 seen when at capacity
            raise SourceError("Overpass is busy or rate-limiting; try again shortly")
        if resp.status_code != 200:
            raise SourceError(f"Overpass returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SourceError("Overpass returned a response that is not JSON") from exc
        if body.get("remark"):  # e.g. a server-side timeout with partial data
            raise SourceError(f"Overpass: {str(body['remark'])[:160]}")
        return parse_elements(body.get("elements", []))

    value, cached = await cached_fetch(
        settings.cache_dir, "overpass_buildings", {"fips": fips}, 7 * DAY, fetch
    )
    return value, cached
