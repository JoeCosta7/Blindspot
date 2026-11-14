"""Shared helpers for ArcGIS REST feature services (EPA ATTAINS, BTS transit
stops): the county polygon as an Esri geometry, and a POST query that turns
service errors into SourceError."""

from __future__ import annotations

import json
from typing import Any

import httpx
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from app.sources import SourceError

# About 500 m. Keeps a county outline to a few dozen vertices so the request
# stays small; features within a few hundred meters of the border may be
# counted in the wrong county.
SIMPLIFY_DEGREES = 0.005


def esri_polygon(geometry: BaseGeometry) -> dict[str, Any]:
    """The county outline, simplified, as an Esri JSON polygon in WGS 84.
    Esri wants outer rings clockwise and holes counter-clockwise."""
    simple = geometry.simplify(SIMPLIFY_DEGREES, preserve_topology=True)
    parts = list(getattr(simple, "geoms", [simple]))
    rings: list[list[list[float]]] = []
    for part in parts:
        if part.geom_type != "Polygon" or part.is_empty:
            continue
        part = orient(part, sign=-1.0)
        rings.append([[round(x, 5), round(y, 5)] for x, y in part.exterior.coords])
        rings += [[[round(x, 5), round(y, 5)] for x, y in hole.coords] for hole in part.interiors]
    return {"rings": rings, "spatialReference": {"wkid": 4326}}


def within(geometry: BaseGeometry) -> dict[str, str]:
    """Query parameters selecting features that intersect the county."""
    return {
        "geometry": json.dumps(esri_polygon(geometry)),
        "geometryType": "esriGeometryPolygon",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def count_by(field: str) -> dict[str, str]:
    """Query parameters for a feature count grouped by one field."""
    return {
        "where": "1=1",
        "outStatistics": json.dumps(
            [
                {
                    "statisticType": "count",
                    "onStatisticField": "OBJECTID",
                    "outStatisticFieldName": "n",
                }
            ]
        ),
        "groupByFieldsForStatistics": field,
    }


async def query(
    client: httpx.AsyncClient, url: str, params: dict[str, str], timeout_s: float, what: str
) -> dict[str, Any]:
    try:
        resp = await client.post(url, data={**params, "f": "json"}, timeout=timeout_s)
    except httpx.HTTPError as exc:
        raise SourceError(f"{what} request failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise SourceError(f"{what} returned HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise SourceError(f"{what} returned a response that is not JSON") from exc
    if "error" in body:
        message = body["error"].get("message") if isinstance(body["error"], dict) else None
        raise SourceError(f"{what} error: {message or body['error']}")
    return body


def grouped_counts(body: dict[str, Any], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in body.get("features", []):
        attributes = feature.get("attributes", {})
        key = attributes.get(field)
        counts[str(key)] = counts.get(str(key), 0) + int(attributes.get("n") or 0)
    return counts
