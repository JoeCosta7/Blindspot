"""Vexcel API 2.0: parcel and structure records, object detection, imagery and
semantic search over aerial imagery.

Checked with live calls on 2026-11-14 (hackathon key; the OpenAPI spec is at
GET /v2/api-docs with the key):
- Base URL https://api.vexcelgroup.com/v2, key in the `X-API-KEY` header.
- GET /property/extract?wkt=POINT(lon lat)&srid=4326&layer=...&latest=true
  &include=precomputed&unit-system=metric returns a GeoJSON FeatureCollection.
  The first feature is the parcel containing the point; its `properties` are
  flat dotted keys ("structure.count", "parcel-tree-cover.percent",
  "hardscapes.area", "vehicles.count", "metadata.gsd",
  "metadata.ortho-date-estimate", "request-information.inside-parcel") plus a
  `structures` list with "footprint.wkt", "footprint.area" (m² when metric),
  "primary-structure", "building-type.class",
  "roof-characteristics.shape.value" (e.g. flat, hip),
  "roof-characteristics.material.class" (shingle, tile, metal, membrane,
  shake, gravel), "roof-characteristics.condition" (1-5),
  "roof-characteristics.tree-cover.percent", "roof-objects.solar.value",
  "roof-objects.ac-unit.count". 404 means no parcel at the point. A polygon
  wkt returns every parcel in it, paged (page, page-size up to 500); filter
  parameters include roof-material and roof-solar.
- POST /elements/detect runs open-vocabulary detection (model "sam3") on an
  ortho extract of the given wkt. Each detection has `bbox` (a WKT polygon in
  map coordinates), `confidence`, `pixel-bbox` and `imageId`; `images[]`
  carries the image geometry, width, height and capture dates.
- POST /ortho/extract returns image bytes; `drawing` draws outlines
  server-side; `destination-dimensions` such as "800,800" caps the size.
- POST /embedding/extract with `search-text` ranks ~60 m imagery tiles in a
  polygon by similarity. Scores are relative within one search.
- 429 means too fast; retry with backoff. Rate limits are not published.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

BASE = "https://api.vexcelgroup.com/v2"
# Every imagery layer, so the API picks the best available at each place.
LAYERS = "urban,urban-r,wide-area,urban-g"
SOURCE_LABEL = "Vexcel aerial imagery and property data"
IMAGE_DIMENSIONS = "800,800"


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.vexcel_api_key:
        raise SourceError("VEXCEL_API_KEY is not set")
    return {"X-API-KEY": settings.vexcel_api_key}


async def _send(
    client: httpx.AsyncClient,
    settings: Settings,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float,
) -> httpx.Response:
    """One call with up to three retries on 429. Raises SourceError on a
    transport error or a 5xx; the caller reads other statuses."""
    headers = _headers(settings)
    for attempt in range(4):
        try:
            resp = await client.request(
                method, f"{BASE}/{path}", headers=headers, params=params, json=body, timeout=timeout
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"Vexcel request failed: {type(exc).__name__}") from exc
        if resp.status_code == 429 and attempt < 3:
            await asyncio.sleep(2**attempt)
            continue
        if resp.status_code == 401:
            raise SourceError("Vexcel rejected the API key")
        if resp.status_code >= 500:
            raise SourceError(f"Vexcel returned HTTP {resp.status_code}")
        return resp
    raise SourceError("Vexcel rate limit reached")


def square_wkt(lat: float, lon: float, half_m: float) -> str:
    """A WKT square centred on the point, `half_m` metres from centre to edge."""
    dlat = half_m / 111_320
    dlon = half_m / (111_320 * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:20]


# --- parcels and structures -----------------------------------------------------


async def property_at(
    client: httpx.AsyncClient, settings: Settings, lat: float, lon: float
) -> dict[str, Any] | None:
    """The parcel containing the point, as a GeoJSON feature with every
    precomputed attribute, or None when there is no parcel there."""
    key = {"lat": round(lat, 5), "lon": round(lon, 5)}

    async def fetch() -> dict[str, Any] | None:
        resp = await _send(
            client,
            settings,
            "GET",
            "property/extract",
            params={
                "wkt": f"POINT({lon} {lat})",
                "srid": 4326,
                "layer": LAYERS,
                "latest": "true",
                "include": "precomputed",
                "unit-system": "metric",
            },
            timeout=settings.source_timeout_s,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise SourceError(f"Vexcel property/extract returned HTTP {resp.status_code}")
        features = resp.json().get("features") or []
        return features[0] if features else None

    value, _ = await cached_fetch(settings.cache_dir, "vexcel_property", key, 30 * DAY, fetch)
    return value


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def parcel_facts(feature: dict[str, Any]) -> dict[str, Any]:
    """The few attributes the scout uses, with our names. The primary
    structure is used; a parcel with none gives footprint fields of None."""
    props = feature.get("properties") or {}
    structures = props.get("structures") or []
    primary = next((s for s in structures if s.get("primary-structure")), None)
    if primary is None and structures:
        primary = max(structures, key=lambda s: _number(s.get("footprint.area")) or 0)
    s = primary or {}
    date = props.get("metadata.ortho-date-estimate")
    return {
        "parcel_wkt": _geometry_wkt(feature.get("geometry")),
        "structure_count": props.get("structure.count"),
        "footprint_wkt": s.get("footprint.wkt"),
        "footprint_m2": _number(s.get("footprint.area")),
        "building_type": s.get("building-type.class"),
        "roof_shape": s.get("roof-characteristics.shape.value"),
        "roof_material": s.get("roof-characteristics.material.class"),
        "roof_condition": _number(s.get("roof-characteristics.condition")),
        "roof_tree_cover_pct": _number(s.get("roof-characteristics.tree-cover.percent")),
        "solar_present": s.get("roof-objects.solar.value"),
        "ac_units": s.get("roof-objects.ac-unit.count"),
        "parcel_tree_cover_pct": _number(props.get("parcel-tree-cover.percent")),
        "hardscape_m2": _number(props.get("hardscapes.area")),
        "hardscape_pct": _number(props.get("hardscapes.percent")),
        "vehicles_count": props.get("vehicles.count"),
        "gsd_m": _number(props.get("metadata.gsd")),
        "captured": str(date)[:10] if date else None,
        "collection": props.get("metadata.collection"),
        "is_ortho": props.get("metadata.is-ortho-image"),
        "inside_parcel": props.get("request-information.inside-parcel"),
    }


def _geometry_wkt(geometry: Any) -> str | None:
    if geometry is None:
        return None
    if isinstance(geometry, str):
        return geometry
    from shapely.geometry import shape

    return shape(geometry).wkt


# --- detection -----------------------------------------------------------------


async def detect(
    client: httpx.AsyncClient,
    settings: Settings,
    area_wkt: str,
    labels: list[str],
    threshold: float = 0.5,
) -> dict[str, Any]:
    """Objects of the given plain-language labels inside the area, found on
    the ortho image. Returns {"image": {...metadata}, "detections": [...]}
    where each detection has label, wkt (map-coordinate box), confidence."""
    key = {"wkt": _digest(area_wkt), "labels": sorted(labels), "threshold": threshold}

    async def fetch() -> dict[str, Any]:
        resp = await _send(
            client,
            settings,
            "POST",
            "elements/detect",
            body={
                "wkt": area_wkt,
                "srid": 4326,
                "buffer-meter": 0,
                "images": [
                    {
                        "endpoint": "ortho/extract",
                        "request": {
                            "layer": LAYERS,
                            "srid": 4326,
                            "wkt": area_wkt,
                            "crop": "bounding_box",
                        },
                    }
                ],
                "model-input": {
                    "elements": [
                        {"label": label, "model": "sam3", "detection-threshold": threshold}
                        for label in labels
                    ],
                    "include": ["bbox", "confidence", "pixel-bbox"],
                },
            },
            timeout=settings.scout_detect_timeout_s,
        )
        if resp.status_code != 200:
            raise SourceError(f"Vexcel elements/detect returned HTTP {resp.status_code}")
        body = resp.json()
        images = body.get("images") or []
        detections = [
            {
                "label": feature["properties"].get("label"),
                "wkt": d.get("bbox"),
                "confidence": d.get("confidence"),
            }
            for feature in body.get("features") or []
            for d in feature["properties"].get("detections") or []
            if d.get("bbox")
        ]
        return {"image": (images[0].get("metadata") if images else None), "detections": detections}

    value, _ = await cached_fetch(settings.cache_dir, "vexcel_detect", key, 30 * DAY, fetch)
    return value


# --- imagery -------------------------------------------------------------------


def image_path(settings: Settings, name: str) -> Path:
    return settings.cache_dir / "scout_images" / f"{name}.png"


async def ortho_png(
    client: httpx.AsyncClient,
    settings: Settings,
    area_wkt: str,
    drawings: list[dict[str, Any]],
    name: str,
) -> Path:
    """A top-down PNG of the area with the outlines drawn by the server, saved
    under the cache directory as <name>.png. Returns the path."""
    path = image_path(settings, name)
    if path.exists():
        return path
    resp = await _send(
        client,
        settings,
        "POST",
        "ortho/extract",
        body={
            "wkt": area_wkt,
            "srid": 4326,
            "layer": LAYERS,
            "buffer-meter": 10,
            "crop": "bounding_box",
            "image-format": "png",
            "destination-dimensions": IMAGE_DIMENSIONS,
            "drawing": drawings,
        },
        timeout=settings.scout_detect_timeout_s,
    )
    if resp.status_code != 200 or not resp.content.startswith(b"\x89PNG"):
        raise SourceError(f"Vexcel ortho/extract returned HTTP {resp.status_code}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(resp.content)
    tmp.replace(path)
    return path


# --- semantic search -----------------------------------------------------------


async def embedding_search(
    client: httpx.AsyncClient, settings: Settings, area_wkt: str, text: str, limit: int
) -> list[dict[str, Any]]:
    """Imagery tiles in the area that best match the text, best first:
    [{"score": float, "wkt": tile polygon, "collection": str}]."""
    key = {"wkt": _digest(area_wkt), "text": text.strip().lower(), "limit": limit}

    async def fetch() -> list[dict[str, Any]]:
        resp = await _send(
            client,
            settings,
            "POST",
            "embedding/extract",
            body={
                "wkt": area_wkt,
                "srid": 4326,
                "search-text": text,
                "include": "score,geometry,collection",
                "total-records": limit,
                "page-size": limit,
            },
            timeout=settings.scout_detect_timeout_s,
        )
        if resp.status_code != 200:
            raise SourceError(f"Vexcel embedding/extract returned HTTP {resp.status_code}")
        return [
            {
                "score": f["properties"].get("score"),
                "wkt": _geometry_wkt(f.get("geometry")),
                "collection": f["properties"].get("collection"),
            }
            for f in resp.json().get("features") or []
        ]

    value, _ = await cached_fetch(settings.cache_dir, "vexcel_embedding", key, 7 * DAY, fetch)
    return value
