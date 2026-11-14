"""The data layers the scout works with, behind one protocol.

Two real layers exist (sources/matterport.py and sources/vexcel.py); the
protocol lets tests inject an in-memory fake. Field names here are ours and
are filled only from fields the vendor APIs were seen to return on
2026-11-14 (see the source modules). A CoStar layer would slot in as a third
source when its API is available: VERIFY before writing it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
from shapely import wkt as shapely_wkt
from shapely.geometry import Point

from app.config import Settings
from app.geo import County
from app.sources import costar, matterport, vexcel

log = logging.getLogger(__name__)

FoundBy = Literal["costar", "matterport", "vexcel"]


@dataclass(frozen=True)
class Building:
    """A candidate building. From a CoStar property record (id "cs:<id>"),
    a 3D scan (id "mp:<model id>") or a parcel found in the imagery
    (id "vx:<lat>,<lon>")."""

    id: str
    name: str
    lat: float
    lon: float
    found_by: FoundBy
    address: str | None = None
    scan_id: str | None = None  # Matterport model id, when a scan exists
    floors: int | None = None
    rooms: int | None = None
    room_labels: tuple[str, ...] = ()
    # From the imagery search only: the parcel record's building type.
    building_type: str | None = None
    # From the CoStar record only.
    costar_id: int | None = None
    property_type: str | None = None  # e.g. Industrial, GeneralRetail, Office
    property_subtype: str | None = None  # e.g. Industrial_Warehouse
    building_class: str | None = None  # A, B, C
    gross_sqft: float | None = None
    stories: int | None = None
    year_built: int | None = None
    parking_spaces: int | None = None
    photo_url: str | None = None


@dataclass(frozen=True)
class Detection:
    label: str
    wkt: str  # a box in map coordinates (lon lat)
    confidence: float | None


@dataclass(frozen=True)
class RoofRecord:
    """The Vexcel parcel and structure record plus live object detection."""

    parcel_wkt: str | None
    footprint_wkt: str | None
    footprint_m2: float | None
    building_type: str | None
    roof_shape: str | None
    roof_material: str | None
    roof_condition: float | None  # 1 (worst) to 5
    roof_tree_cover_pct: float | None
    solar_present: bool | None  # the precomputed record's view
    ac_units: int | None  # the precomputed record's count
    parcel_tree_cover_pct: float | None
    hardscape_m2: float | None
    vehicles_count: int | None  # the precomputed record's count
    gsd_m: float | None
    captured: str | None
    collection: str | None
    inside_parcel: bool | None
    detections: tuple[Detection, ...] = ()
    detection_failed: str | None = None


@dataclass(frozen=True)
class InteriorRecord:
    scan_id: str
    name: str | None
    floors: tuple[tuple[str | None, float | None], ...]  # (label, area m²)
    rooms: tuple[tuple[str, float | None], ...]
    total_area_m2: float | None
    classification: str | None
    summary: str | None
    tour_url: str | None
    image_url: str | None


@dataclass
class ScanIndex:
    """The scans inside one county, for lookups by id and by location."""

    buildings: list[Building] = field(default_factory=list)

    def near(self, parcel_wkt: str | None, lat: float, lon: float) -> Building | None:
        """A scan inside the parcel polygon, else within about 40 m of the point."""
        polygon = shapely_wkt.loads(parcel_wkt) if parcel_wkt else None
        best: tuple[float, Building] | None = None
        for b in self.buildings:
            if polygon is not None and polygon.contains(Point(b.lon, b.lat)):
                return b
            d = ((b.lat - lat) * 111_320) ** 2 + ((b.lon - lon) * 96_000) ** 2
            if d < 40**2 and (best is None or d < best[0]):
                best = (d, b)
        return best[1] if best else None


# Labels the detector is asked for on every roof. Specific labels work better
# than vague ones (Vexcel tutorial, 2026-11-14).
DETECT_LABELS = ["rooftop hvac unit", "solar panel", "car"]


class BuildingData(Protocol):
    name: str

    async def scanned_buildings(self, county: County) -> list[Building]:
        """Every building in the county that has a 3D scan."""
        ...

    async def buildings_from_above(
        self, county: County, lat: float, lon: float, half_m: float, text: str, limit: int
    ) -> list[Building]:
        """Buildings found by searching the imagery around a point for `text`."""
        ...

    async def properties(self, county: County) -> list[Building]:
        """Every commercial property record in the county (the CoStar
        layer). Empty when the kit is not installed."""
        ...

    async def roof(self, building: Building) -> RoofRecord | None:
        """Parcel, structure and detected objects, or None with no parcel."""
        ...

    async def interior(self, building: Building) -> InteriorRecord | None:
        """The scan's floors, rooms and summary, or None without a scan."""
        ...

    async def image(
        self, building: Building, roof: RoofRecord
    ) -> tuple[str, Literal["oblique", "ortho"]] | None:
        """Save a photo of the building with the outlines drawn: a side-angle
        view where one exists, else top-down. Returns (name the image endpoint
        serves, view), or None."""
        ...


class RealBuildingData:
    name = "real"

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self._scans: dict[str, ScanIndex] = {}

    async def properties(self, county: County) -> list[Building]:
        kit = self.settings.costar_kit_dir
        if not costar.available(kit):
            return []
        return [
            Building(
                id=f"cs:{r['id']}",
                name=r["name"],
                lat=r["lat"],
                lon=r["lon"],
                found_by="costar",
                address=r["address"],
                scan_id=r["matterport_ids"][0] if r["matterport_ids"] else None,
                floors=r["stories"],
                costar_id=r["id"],
                property_type=r["property_type"],
                property_subtype=r["property_subtype"],
                building_class=r["building_class"],
                gross_sqft=r["gross_sqft"],
                stories=r["stories"],
                year_built=r["year_built"],
                parking_spaces=r["parking_spaces"],
                photo_url=r["photo_url"],
            )
            for r in costar.properties_in_county(kit, county)
            if r["construction_status"] in (None, "Existing", "UnderRenovation", "Redeveloped")
        ]

    async def scanned_buildings(self, county: County) -> list[Building]:
        if county.fips in self._scans:
            return self._scans[county.fips].buildings
        entries, _ = await matterport.state_index(self.client, self.settings, county.state)
        inside = county.contains_points([e["lon"] for e in entries], [e["lat"] for e in entries])
        buildings = [
            Building(
                id=f"mp:{e['id']}",
                name=e["name"] or e["address"] or f"Scan {e['id']}",
                lat=e["lat"],
                lon=e["lon"],
                found_by="matterport",
                address=e["address"],
                scan_id=e["id"],
                floors=e["floors"],
                rooms=e["rooms"],
                room_labels=tuple(e["room_labels"]),
            )
            for e, keep in zip(entries, inside, strict=True)
            if keep
        ]
        self._scans[county.fips] = ScanIndex(buildings)
        return buildings

    async def buildings_from_above(
        self, county: County, lat: float, lon: float, half_m: float, text: str, limit: int
    ) -> list[Building]:
        tiles = await vexcel.embedding_search(
            self.client, self.settings, vexcel.square_wkt(lat, lon, half_m), text, limit
        )
        scans = self._scans.get(county.fips) or ScanIndex()
        found: list[Building] = []
        for tile in tiles:
            if not tile.get("wkt"):
                continue
            centre = shapely_wkt.loads(tile["wkt"]).centroid
            feature = await vexcel.property_at(self.client, self.settings, centre.y, centre.x)
            if feature is None:
                continue
            facts = vexcel.parcel_facts(feature)
            if not facts["footprint_wkt"]:
                continue
            roof_centre = shapely_wkt.loads(facts["footprint_wkt"]).centroid
            scan = scans.near(facts["parcel_wkt"], roof_centre.y, roof_centre.x)
            kind = (facts["building_type"] or "building").replace("_", " ")
            found.append(
                Building(
                    id=f"vx:{roof_centre.y:.5f},{roof_centre.x:.5f}",
                    name=scan.name if scan else f"Unnamed {kind}",
                    lat=roof_centre.y,
                    lon=roof_centre.x,
                    found_by="vexcel",
                    address=scan.address if scan else None,
                    scan_id=scan.scan_id if scan else None,
                    floors=scan.floors if scan else None,
                    rooms=scan.rooms if scan else None,
                    room_labels=scan.room_labels if scan else (),
                    building_type=facts["building_type"],
                )
            )
        return found

    async def roof(self, building: Building) -> RoofRecord | None:
        feature = await vexcel.property_at(self.client, self.settings, building.lat, building.lon)
        if feature is None:
            return None
        f = vexcel.parcel_facts(feature)
        detections: tuple[Detection, ...] = ()
        failed: str | None = None
        # Rooftop equipment is small, and the detector downsamples large areas,
        # so it runs on the footprint; cars are found on the whole parcel.
        jobs = []
        if f["footprint_wkt"]:
            jobs.append((f["footprint_wkt"], ["rooftop hvac unit", "solar panel"]))
        if f["parcel_wkt"]:
            jobs.append((f["parcel_wkt"], ["car"]))
        found: list[Detection] = []
        for job_area, labels in jobs:
            try:
                result = await vexcel.detect(self.client, self.settings, job_area, labels)
                found += [
                    Detection(label=d["label"], wkt=d["wkt"], confidence=d.get("confidence"))
                    for d in result["detections"]
                ]
            except Exception as exc:  # detection is an extra; the record still counts
                failed = str(exc)[:120]
        detections = tuple(found)
        return RoofRecord(
            parcel_wkt=f["parcel_wkt"],
            footprint_wkt=f["footprint_wkt"],
            footprint_m2=f["footprint_m2"],
            building_type=f["building_type"],
            roof_shape=f["roof_shape"],
            roof_material=f["roof_material"],
            roof_condition=f["roof_condition"],
            roof_tree_cover_pct=f["roof_tree_cover_pct"],
            solar_present=f["solar_present"],
            ac_units=f["ac_units"],
            parcel_tree_cover_pct=f["parcel_tree_cover_pct"],
            hardscape_m2=f["hardscape_m2"],
            vehicles_count=f["vehicles_count"],
            gsd_m=f["gsd_m"],
            captured=f["captured"],
            collection=f["collection"],
            inside_parcel=f["inside_parcel"],
            detections=detections,
            detection_failed=failed,
        )

    async def interior(self, building: Building) -> InteriorRecord | None:
        if not building.scan_id:
            return None
        d = await matterport.model_detail(self.client, self.settings, building.scan_id)
        if d is None:
            return None
        return InteriorRecord(
            scan_id=d["id"],
            name=d["name"],
            floors=tuple((f["label"], f["area_m2"]) for f in d["floors"]),
            rooms=tuple((r["label"], r["area_m2"]) for r in d["rooms"]),
            total_area_m2=d["total_area_m2"],
            classification=d["classification"],
            summary=d["summary"],
            tour_url=d["tour_url"],
            image_url=d["image_url"],
        )

    async def image(
        self, building: Building, roof: RoofRecord
    ) -> tuple[str, Literal["oblique", "ortho"]] | None:
        area = roof.parcel_wkt or roof.footprint_wkt
        if not area:
            return None
        drawings: list[dict[str, Any]] = []
        if roof.parcel_wkt:
            drawings.append(
                {"wkt": roof.parcel_wkt, "outline-width": 3, "outline-color": "rgb(255,204,0)"}
            )
        if roof.footprint_wkt:
            drawings.append(
                {"wkt": roof.footprint_wkt, "outline-width": 4, "outline-color": "rgb(80,255,80)"}
            )
        colors = {"rooftop hvac unit": "rgb(255,64,255)", "solar panel": "rgb(255,120,0)"}
        for d in roof.detections:
            color = colors.get(d.label, "rgb(0,229,255)")
            drawings.append({"wkt": d.wkt, "outline-width": 2, "outline-color": color})
        name = building.id.replace(":", "_").replace(",", "_")
        # A side-angle photo shows the building itself; fall back to top-down.
        try:
            directions = await vexcel.oblique_directions(self.client, self.settings, area)
            if directions:
                await vexcel.oblique_jpg(
                    self.client, self.settings, area, directions[0], drawings, name
                )
                return name, "oblique"
        except Exception as exc:  # the oblique is a nicety; the ortho still works
            log.warning("oblique image for %s failed: %r", building.id, exc)
        await vexcel.ortho_png(self.client, self.settings, area, drawings, name)
        return name, "ortho"


def get_provider(client: httpx.AsyncClient, settings: Settings) -> BuildingData:
    return RealBuildingData(client, settings)
