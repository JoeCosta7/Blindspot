"""County index: FIPS lookup, box selection and point-in-polygon tests."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree


@dataclass(frozen=True, eq=False)
class County:
    fips: str
    name: str  # "Broome County"
    state: str  # "NY"
    state_fips: str  # "36"
    geometry: BaseGeometry
    centroid_lon: float
    centroid_lat: float

    @property
    def county_code(self) -> str:
        return self.fips[2:]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(west, south, east, north)"""
        return self.geometry.bounds

    def contains_points(self, lons: Sequence[float], lats: Sequence[float]) -> np.ndarray:
        if len(lons) == 0:
            return np.zeros(0, dtype=bool)
        return shapely.contains_xy(self.geometry, np.asarray(lons), np.asarray(lats))


class CountyIndex:
    def __init__(self, counties: Sequence[County]) -> None:
        self._counties = list(counties)
        self._by_fips = {c.fips: c for c in self._counties}
        self._tree = STRtree([c.geometry for c in self._counties])

    @classmethod
    def from_geojson(cls, path: Path) -> CountyIndex:
        collection = json.loads(path.read_text())
        counties = []
        for feature in collection["features"]:
            props = feature["properties"]
            geometry = shape(feature["geometry"])
            if not geometry.is_valid:
                geometry = shapely.make_valid(geometry)
            counties.append(
                County(
                    fips=props["GEOID"],
                    name=props.get("NAMELSAD") or props["NAME"],
                    state=props["STUSPS"],
                    state_fips=props.get("STATEFP") or props["GEOID"][:2],
                    geometry=geometry,
                    centroid_lon=float(props["centroid_lon"]),
                    centroid_lat=float(props["centroid_lat"]),
                )
            )
        return cls(counties)

    def __len__(self) -> int:
        return len(self._counties)

    def get(self, fips: str) -> County | None:
        return self._by_fips.get(fips)

    def resolve_bbox(
        self,
        sw: tuple[float, float],
        ne: tuple[float, float],
        max_counties: int,
        min_area_share: float = 0.25,
    ) -> tuple[list[County], bool]:
        """Counties selected by a box, nearest to the box's center first, and
        whether the list was cut to `max_counties`.

        A county is kept if its centroid is inside the box or at least
        `min_area_share` of its area is. Of those, the ones whose centroids
        are nearest the center of the box are returned. Corners may arrive in
        any order.
        """
        west, east = sorted((sw[0], ne[0]))
        south, north = sorted((sw[1], ne[1]))
        selection = box(west, south, east, north)
        center_lon, center_lat = (west + east) / 2, (south + north) / 2
        # A degree of longitude shrinks toward the poles; scale it so distance
        # east-west counts the same as north-south at the box's latitude.
        lon_scale = math.cos(math.radians(center_lat))

        matches: list[tuple[float, County]] = []
        for i in self._tree.query(selection, predicate="intersects"):
            county = self._counties[int(i)]
            overlap = county.geometry.intersection(selection).area
            if overlap <= 0:
                continue
            centroid_inside = (
                west <= county.centroid_lon <= east and south <= county.centroid_lat <= north
            )
            share = overlap / county.geometry.area
            if centroid_inside or share >= min_area_share:
                distance = math.hypot(
                    (county.centroid_lon - center_lon) * lon_scale,
                    county.centroid_lat - center_lat,
                )
                matches.append((distance, county))

        matches.sort(key=lambda m: (m[0], m[1].fips))
        truncated = len(matches) > max_counties
        return [county for _, county in matches[:max_counties]], truncated
