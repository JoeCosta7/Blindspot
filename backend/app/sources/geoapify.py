"""Public buildings from the Geoapify Places API: schools, libraries and
colleges in a bounding box. The pipeline filters the result by county polygon.
About 1.5 s per request.

Confirmed with live calls on 2026-11-14:
- `categories`, `filter=rect:west,south,east,north`, `limit`, `apiKey`.
- A request returns at most 500 places and `offset` does not page past that
  (offset 500 came back empty for Cook County schools). So a box that comes
  back full is split into four and each quarter is requested again.
- Each feature carries a Geoapify `place_id`, used as the asset id.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

URL = "https://api.geoapify.com/v2/places"
SOURCE_LABEL = "Geoapify Places"

# Geoapify category -> our asset type. Checked in this order, so a place
# tagged as both a library and a college counts as a library.
CATEGORY_TYPES = {
    "education.library": "library",
    "education.college": "college",
    "education.university": "college",
    "education.school": "school",
}
PAGE_LIMIT = 500  # the API's maximum per request
# A full box is split into quarters at most this many times. Depth 5 gives
# boxes about 1/32 of the county's width, enough for the largest US counties.
MAX_SPLIT_DEPTH = 5

# Requests in flight at once, shared by every county in a report, so a big
# county's split boxes do not burst past the free tier's rate limit.
_request_slots = asyncio.Semaphore(4)

Box = tuple[float, float, float, float]  # west, south, east, north


def parse_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Named places with a location. Unnamed ones are dropped: they cannot be
    shown to an official as a place."""
    buildings = []
    for feature in features:
        props = feature.get("properties") or {}
        categories = props.get("categories") or []
        asset_type = next((t for c, t in CATEGORY_TYPES.items() if c in categories), None)
        name = (props.get("name") or "").strip()
        if not asset_type or not name or props.get("lat") is None or props.get("lon") is None:
            continue
        place_id = props.get("place_id")
        if not place_id:
            continue  # no stable id to dedupe or link by
        buildings.append(
            {
                "place_id": str(place_id),
                "type": asset_type,
                "name": name,
                "lat": float(props["lat"]),
                "lon": float(props["lon"]),
                "city": props.get("city"),
            }
        )
    return buildings


def split_box(box: Box) -> list[Box]:
    west, south, east, north = box
    mid_lon, mid_lat = (west + east) / 2, (south + north) / 2
    return [
        (west, south, mid_lon, mid_lat),
        (mid_lon, south, east, mid_lat),
        (west, mid_lat, mid_lon, north),
        (mid_lon, mid_lat, east, north),
    ]


async def _request_box(
    client: httpx.AsyncClient, settings: Settings, box: Box
) -> list[dict[str, Any]]:
    west, south, east, north = box
    params = {
        "categories": ",".join(CATEGORY_TYPES),
        "filter": f"rect:{west},{south},{east},{north}",
        "limit": PAGE_LIMIT,
        "apiKey": settings.geoapify_api_key,
    }
    async with _request_slots:
        try:
            resp = await client.get(URL, params=params, timeout=settings.source_timeout_s)
        except httpx.HTTPError as exc:
            raise SourceError(f"Geoapify request failed: {type(exc).__name__}") from exc
    if resp.status_code == 401:
        raise SourceError("Geoapify rejected GEOAPIFY_API_KEY (HTTP 401)")
    if resp.status_code == 429:
        raise SourceError("Geoapify rate limit or daily quota reached")
    if resp.status_code != 200:
        raise SourceError(f"Geoapify returned HTTP {resp.status_code}")
    try:
        return resp.json().get("features", [])
    except ValueError as exc:
        raise SourceError("Geoapify returned a response that is not JSON") from exc


async def _fetch_box(
    client: httpx.AsyncClient, settings: Settings, box: Box, depth: int = 0
) -> list[dict[str, Any]]:
    features = await _request_box(client, settings, box)
    if len(features) < PAGE_LIMIT or depth >= MAX_SPLIT_DEPTH:
        return features
    quarters = await asyncio.gather(
        *(_fetch_box(client, settings, q, depth + 1) for q in split_box(box))
    )
    return [f for quarter in quarters for f in quarter]


async def fetch_public_buildings(
    client: httpx.AsyncClient,
    settings: Settings,
    fips: str,
    bounds: tuple[float, float, float, float],
) -> tuple[list[dict[str, Any]], bool]:
    """`bounds` is (west, south, east, north). Cached per county."""

    async def fetch() -> list[dict[str, Any]]:
        if not settings.geoapify_api_key:
            raise SourceError("GEOAPIFY_API_KEY is not set")
        features = await _fetch_box(client, settings, bounds)
        # A place on the edge between two split boxes can come back twice.
        unique = {}
        for building in parse_features(features):
            unique.setdefault(building["place_id"], building)
        return list(unique.values())

    value, cached = await cached_fetch(
        # v2: rows are keyed by `place_id`. Older cache entries used another key.
        settings.cache_dir,
        "geoapify_buildings_v2",
        {"fips": fips},
        7 * DAY,
        fetch,
    )
    return value, cached
