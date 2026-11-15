"""Matterport Model API: the 3D scans ("models") an account can see, with
their addresses, coordinates, floors, rooms and an AI-written summary.

Checked with live calls on 2026-11-14 (hackathon account, 14,945 models:
6,034 with locality Houston, 4,747 Los Angeles):
- GraphQL at https://api.matterport.com/api/models/graph, HTTP Basic auth
  with the API token id as the username and the token secret as the password
  (docs: https://developers.matterport.com/api/).
- `models(query, pageSize, offset)` searches with an Elasticsearch
  query-string, e.g. address.administrativeArea:"TX". pageSize up to 1000;
  pass `nextOffset` back as `offset`. There is no documented geo-distance
  search, so a state index is pulled and filtered by county polygon here.
- Search results carry id, name, geolocation { lat long } (strings),
  address { streetAddressLines locality administrativeArea postalCode },
  rooms { count labels }, floors { count }, image { url }.
- `model(id)` adds floors and rooms with dimensionEstimates(units: metric)
  { area areaIndoor } (0 or null on many models), propertyClassifications
  { classification } (e.g. factory_industrial, education),
  generatedDescription { summary } and publication { url embed }, where url
  is https://my.matterport.com/show/?m=<id>.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

log = logging.getLogger(__name__)

URL = "https://api.matterport.com/api/models/graph"
SOURCE_LABEL = "Matterport 3D scans"
PAGE_SIZE = 1000
MAX_PAGES = 12

INDEX_QUERY = """
query($q: String!, $offset: String) {
  models(query: $q, pageSize: PAGE_SIZE, offset: $offset) {
    totalResults nextOffset
    results {
      id name
      geolocation { lat long }
      address { streetAddressLines locality administrativeArea postalCode }
      rooms { count labels }
      floors { count }
      image { url }
    }
  }
}
""".replace("PAGE_SIZE", str(PAGE_SIZE))

DETAIL_QUERY = """
query($id: ID!) {
  model(id: $id) {
    id name
    dimensionEstimates(units: metric) { area areaIndoor }
    floors { label sequence dimensionEstimates(units: metric) { area areaIndoor } }
    rooms { label dimensionEstimates(units: metric) { area } ceiling(units: metric) { maxHeight } }
    propertyClassifications { classification }
    generatedDescription { summary }
    publication { url }
    image { url }
  }
}
"""


async def _gql(
    client: httpx.AsyncClient, settings: Settings, query: str, variables: dict[str, Any]
) -> dict[str, Any]:
    if not (settings.matterport_api_key and settings.matterport_api_secret):
        raise SourceError("MATTERPORT_API_KEY or MATTERPORT_API_SECRET is not set")
    try:
        resp = await client.post(
            URL,
            json={"query": query, "variables": variables},
            auth=(settings.matterport_api_key, settings.matterport_api_secret),
            headers={"content-type": "application/json"},
            timeout=settings.source_timeout_s,
        )
    except httpx.HTTPError as exc:
        raise SourceError(f"Matterport request failed: {type(exc).__name__}") from exc
    if resp.status_code == 401:
        raise SourceError("Matterport rejected the API token")
    if resp.status_code != 200:
        raise SourceError(f"Matterport returned HTTP {resp.status_code}")
    body = resp.json()
    if body.get("errors"):
        message = str(body["errors"][0].get("message", ""))[:160]
        raise SourceError(f"Matterport query error: {message}")
    return body.get("data") or {}


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _entry(result: dict[str, Any]) -> dict[str, Any] | None:
    geo = result.get("geolocation") or {}
    lat, lon = _float(geo.get("lat")), _float(geo.get("long"))
    if lat is None or lon is None:
        return None
    address = result.get("address") or {}
    lines = [line for line in address.get("streetAddressLines") or [] if line]
    rooms = result.get("rooms") or {}
    return {
        "id": result["id"],
        "name": (result.get("name") or "").strip() or None,
        "lat": lat,
        "lon": lon,
        "address": ", ".join(lines) or None,
        "locality": address.get("locality"),
        "postal_code": address.get("postalCode"),
        "rooms": rooms.get("count"),
        "room_labels": [label for label in rooms.get("labels") or [] if label],
        "floors": (result.get("floors") or {}).get("count"),
        "image_url": (result.get("image") or {}).get("url"),
    }


async def state_index(
    client: httpx.AsyncClient, settings: Settings, state: str
) -> tuple[list[dict[str, Any]], bool]:
    """Every model in the state that has coordinates, with the search-result
    fields above. Cached for 7 days. (Models whose address lacks a state are
    missed; a few hundred of the Houston models are in that case.)"""
    key = {"state": state}

    async def fetch() -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        offset: str | None = None
        for _ in range(MAX_PAGES):
            data = await _gql(
                client,
                settings,
                INDEX_QUERY,
                {"q": f'address.administrativeArea:"{state}"', "offset": offset},
            )
            page = data.get("models") or {}
            for result in page.get("results") or []:
                entry = _entry(result)
                if entry is not None:
                    entries.append(entry)
            offset = page.get("nextOffset")
            if not offset or len(page.get("results") or []) < PAGE_SIZE:
                break
        return entries

    return await cached_fetch(settings.cache_dir, "matterport_state", key, 7 * DAY, fetch)


def _area(dims: dict[str, Any] | None) -> float | None:
    value = _float((dims or {}).get("area"))
    return value if value and value > 0 else None


async def model_detail(
    client: httpx.AsyncClient, settings: Settings, model_id: str
) -> dict[str, Any] | None:
    """Floors, rooms, classification, summary and tour URL for one model."""
    key = {"id": model_id, "v": 2}  # v2: rooms carry ceiling heights

    async def fetch() -> dict[str, Any] | None:
        model = (await _gql(client, settings, DETAIL_QUERY, {"id": model_id})).get("model")
        if not model:
            return None
        floors = sorted(model.get("floors") or [], key=lambda f: f.get("sequence") or 0)
        return {
            "id": model["id"],
            "name": (model.get("name") or "").strip() or None,
            "total_area_m2": _area(model.get("dimensionEstimates")),
            "floors": [
                {"label": f.get("label"), "area_m2": _area(f.get("dimensionEstimates"))}
                for f in floors
            ],
            # Rooms of 5 m² or more (scans include tiny fragments), largest first,
            # with the ceiling's highest point. Unnamed rooms keep a None label.
            "rooms": sorted(
                (
                    {
                        "label": r.get("label"),
                        "area_m2": _area(r.get("dimensionEstimates")),
                        "ceiling_m": _float((r.get("ceiling") or {}).get("maxHeight")),
                    }
                    for r in model.get("rooms") or []
                    if (_area(r.get("dimensionEstimates")) or 0) >= 5
                ),
                key=lambda r: -(r["area_m2"] or 0),
            ),
            "classification": next(
                (
                    c.get("classification")
                    for c in model.get("propertyClassifications") or []
                    if c.get("classification")
                ),
                None,
            ),
            "summary": (model.get("generatedDescription") or {}).get("summary"),
            "tour_url": (model.get("publication") or {}).get("url"),
            "image_url": (model.get("image") or {}).get("url"),
        }

    value, _ = await cached_fetch(settings.cache_dir, "matterport_model", key, 30 * DAY, fetch)
    return value
