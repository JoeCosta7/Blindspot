"""Measurements, cross-layer checks and the site score. Pure functions.

Every area here is computed from vendor geometry in map coordinates (the
parcel, footprint and detection polygons), never read off an image by a
model. Thresholds come from settings and are our own choices.
"""

from __future__ import annotations

from pyproj import Geod
from shapely import wkt as shapely_wkt
from shapely.geometry.base import BaseGeometry

from app.config import Settings
from app.estimates import format_number
from app.models import LayerCheck, Need
from app.scout.provider import Detection, InteriorRecord, RoofRecord

_GEOD = Geod(ellps="WGS84")


def geodesic_area_m2(geometry: BaseGeometry) -> float:
    """Area on the WGS84 ellipsoid, in square metres."""
    area, _ = _GEOD.geometry_area_perimeter(geometry)
    return abs(area)


def wkt_area_m2(text: str) -> float:
    return geodesic_area_m2(shapely_wkt.loads(text))


def _fmt_m2(value: float) -> str:
    return f"{format_number(round(value))} m²"


# --- obstructions ---------------------------------------------------------------


def detections_on_roof(
    detections: tuple[Detection, ...], footprint_wkt: str | None, label: str
) -> list[Detection]:
    """Detections of one label whose box centre lies on the building footprint."""
    if footprint_wkt is None:
        return []
    footprint = shapely_wkt.loads(footprint_wkt)
    kept = []
    for d in detections:
        if d.label != label:
            continue
        box = shapely_wkt.loads(d.wkt)
        if footprint.contains(box.centroid):
            kept.append(d)
    return kept


def obstruction_m2(detections: list[Detection], footprint_wkt: str | None) -> float:
    """Roof area covered by the given detections, clipped to the footprint and
    merged so overlapping boxes are not counted twice."""
    if not detections or footprint_wkt is None:
        return 0.0
    from shapely.ops import unary_union

    footprint = shapely_wkt.loads(footprint_wkt)
    union = unary_union([shapely_wkt.loads(d.wkt) for d in detections]).intersection(footprint)
    return geodesic_area_m2(union) if not union.is_empty else 0.0


# --- cross-layer checks ------------------------------------------------------------


def footprint_check(
    roof: RoofRecord, interior: InteriorRecord | None, tolerance: float
) -> LayerCheck | None:
    """The scan's largest floor area against the record's footprint. An
    indoor area is smaller than the footprint by the walls, so the check
    passes when the floor is no larger than the footprint and no smaller than
    (1 - tolerance) of it."""
    if interior is None or roof.footprint_m2 is None:
        return None
    floor_areas = [a for _, a in interior.floors if a]
    if not floor_areas:
        return LayerCheck(
            label="Scan floor area vs footprint",
            layers=["matterport", "vexcel"],
            ok=True,
            detail="The scan reports no floor area, so the footprint could not be cross-checked.",
        )
    floor = max(floor_areas)
    low, high = roof.footprint_m2 * (1 - tolerance), roof.footprint_m2 * 1.05
    ok = low <= floor <= high
    gap = abs(floor - roof.footprint_m2) / roof.footprint_m2
    return LayerCheck(
        label="Scan floor area vs footprint",
        layers=["matterport", "vexcel"],
        ok=ok,
        detail=(
            f"Largest scanned floor {_fmt_m2(floor)} vs footprint {_fmt_m2(roof.footprint_m2)} "
            f"from the aerial record ({gap:.0%} apart)"
            + ("" if ok else "; the scan may cover part of the building or another one")
        ),
    )


def solar_check(roof: RoofRecord) -> LayerCheck | None:
    """The record's precomputed 'solar on roof' flag against the live detection."""
    if roof.solar_present is None or roof.detection_failed:
        return None
    panels = detections_on_roof(roof.detections, roof.footprint_wkt, "solar panel")
    detected = len(panels) > 0
    ok = detected == bool(roof.solar_present)
    record = "solar" if roof.solar_present else "no solar"
    seen = f"{len(panels)} panel area(s) detected" if detected else "none detected"
    return LayerCheck(
        label="Existing solar: record vs detection",
        layers=["vexcel"],
        ok=ok,
        detail=f"Precomputed record says {record}; live detection on the roof: {seen}",
    )


def vehicle_check(roof: RoofRecord) -> LayerCheck | None:
    """The record's vehicle count against the cars detected on the parcel."""
    if roof.vehicles_count is None or roof.detection_failed:
        return None
    cars = sum(1 for d in roof.detections if d.label == "car")
    recorded = int(roof.vehicles_count)
    ok = abs(cars - recorded) <= max(3, round(0.5 * max(cars, recorded)))
    return LayerCheck(
        label="Vehicles: record vs detection",
        layers=["vexcel"],
        ok=ok,
        detail=(
            f"Precomputed record counts {recorded} vehicle(s); "
            f"live detection found {cars} car(s)"
        ),
    )


# --- flags -----------------------------------------------------------------------------


def flood_flag(needs: list[Need], interior: InteriorRecord | None) -> str | None:
    """A ground-floor-only building in a county with high flood risk needs
    its electrical gear raised. Written by code from the needs' detail."""
    hazard = next((n for n in needs if n.id == "hazard_risk"), None)
    if hazard is None or not hazard.detail:
        return None
    flood = any(
        word in hazard.detail
        and ("Very High" in hazard.detail or "Relatively High" in hazard.detail)
        for word in ("flooding", "Flooding", "flood")
    )
    if not flood:
        return None
    single = interior is not None and len(interior.floors) == 1
    where = "a single-storey building" if single else "the ground floor"
    return (
        f"Inland or coastal flooding is rated high in this county (FEMA NRI): batteries, "
        f"inverters and switchgear in {where} should be raised or placed above flood level."
    )


# --- score -------------------------------------------------------------------------------


def confidence_factor(
    roof: RoofRecord | None,
    interior: InteriorRecord | None,
    checks: list[LayerCheck],
    settings: Settings,
) -> tuple[float, list[str]]:
    """How much to trust the site, and why. Our own factors (config)."""
    factor = 1.0
    reasons: list[str] = []
    if roof is None or roof.footprint_m2 is None:
        factor *= settings.scout_factor_low_confidence
        reasons.append("no building footprint in the aerial record")
    elif roof.inside_parcel is False:
        factor *= settings.scout_factor_low_confidence
        reasons.append("the building's point fell outside the parcel returned")
    if interior is None:
        factor *= settings.scout_factor_no_scan
        reasons.append("no 3D scan of the interior")
    if any(not c.ok and c.label.startswith("Scan floor area") for c in checks):
        factor *= settings.scout_factor_footprint_mismatch
        reasons.append("the scan's floor area does not match the footprint")
    return factor, reasons
