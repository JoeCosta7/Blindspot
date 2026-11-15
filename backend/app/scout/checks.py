"""Site checks from a table: data/site_checks.json says which checks each
project type gets. Code collects the site's facts from the data layers and
evaluates them; nothing here is written by a model.

A 'fact' check compares one fact with a value; a 'cross' check runs one of
the cross-layer comparisons in measure.py. A fact that is unknown for a site
skips its check rather than failing it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.estimates import format_number
from app.models import LayerCheck, ScoutLayer
from app.scout import measure
from app.scout.provider import Building, InteriorRecord, RoofRecord

# fact -> (label, unit, layer)
FACTS: dict[str, tuple[str, str | None, ScoutLayer]] = {
    "roof_shape": ("Roof shape", None, "vexcel"),
    "roof_material": ("Roof material", None, "vexcel"),
    "roof_condition": ("Roof condition (1-5)", None, "vexcel"),
    "roof_tree_cover_pct": ("Tree cover on the roof", "%", "vexcel"),
    "solar_present": ("Solar on the roof", None, "vexcel"),
    "footprint_m2": ("Building footprint", "m²", "vexcel"),
    "parcel_on_record": ("Parcel on record", None, "vexcel"),
    "parcel_m2": ("Parcel area", "m²", "vexcel"),
    "structure_count": ("Buildings on the parcel", None, "vexcel"),
    "hardscape_m2": ("Paved area", "m²", "vexcel"),
    "parcel_tree_cover_pct": ("Tree cover on the parcel", "%", "vexcel"),
    "vehicles_detected": ("Vehicles detected", None, "vexcel"),
    "scan_exists": ("3D scan of the interior", None, "matterport"),
    "scan_floor_m2": ("Largest scanned floor", "m²", "matterport"),
    "parking_spaces": ("Parking spaces on record", None, "costar"),
    "year_built": ("Year built", None, "costar"),
    "stories": ("Storeys", None, "costar"),
    "gross_m2": ("Floor area on record", "m²", "costar"),
}

OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
    "contains": lambda a, b: str(b).lower() in str(a).lower(),
}
OP_WORDS = {"gte": "at least", "lte": "at most", "eq": "", "ne": "not", "contains": "includes"}


@lru_cache(maxsize=2)
def load_checks(path: Path) -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(path.read_text(encoding="utf-8"))["checks"])


def site_facts(
    roof: RoofRecord | None, interior: InteriorRecord | None, building: Building
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        # Unknown, not "no", without a scan: the check is skipped, not failed.
        "scan_exists": True if interior is not None else None,
        "parking_spaces": building.parking_spaces,
        "year_built": building.year_built,
        "stories": building.stories,
        "gross_m2": building.gross_sqft * 0.09290304 if building.gross_sqft else None,
    }
    if roof is not None:
        on_record = roof.inside_parcel is not None
        facts.update(
            roof_shape=roof.roof_shape,
            roof_material=roof.roof_material,
            roof_condition=roof.roof_condition,
            roof_tree_cover_pct=roof.roof_tree_cover_pct,
            solar_present=roof.solar_present,
            footprint_m2=roof.footprint_m2,
            parcel_on_record=on_record,
            parcel_m2=measure.wkt_area_m2(roof.parcel_wkt)
            if on_record and roof.parcel_wkt
            else None,
            structure_count=roof.structure_count,
            hardscape_m2=roof.hardscape_m2,
            parcel_tree_cover_pct=roof.parcel_tree_cover_pct,
            vehicles_detected=(
                None
                if roof.detection_failed
                else sum(1 for d in roof.detections if d.label == "car")
            ),
        )
    if interior is not None:
        floors = [a for _, a in interior.floors if a]
        facts["scan_floor_m2"] = max(floors) if floors else None
    return facts


def _show(value: Any, unit: str | None) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{format_number(round(value, 1))}{' ' + unit if unit else ''}"
    return str(value).replace("_", " ")


def _fact_check(spec: dict[str, Any], facts: dict[str, Any]) -> LayerCheck | None:
    value = facts.get(spec["fact"])
    if value is None:
        return None
    label, unit, layer = FACTS[spec["fact"]]
    ok = bool(OPS[spec["op"]](value, spec["value"]))
    wanted = f"{OP_WORDS[spec['op']]} {_show(spec['value'], unit)}".strip()
    return LayerCheck(
        label=spec["label"],
        layers=[layer],
        ok=ok,
        detail=f"{label}: {_show(value, unit)} (screening threshold: {wanted}; our own choice)",
    )


def selected(project_ids: set[str], check_ids: tuple[str, ...], path: Path) -> list[dict[str, Any]]:
    """The table rows that apply: by id when given, else by project."""
    rows = []
    for spec in load_checks(path):
        if check_ids:
            if spec["id"] in check_ids:
                rows.append(spec)
        elif "*" in spec["projects"] or set(spec["projects"]) & project_ids:
            rows.append(spec)
    return rows


def detection_labels(rows: list[dict[str, Any]]) -> set[str]:
    """What live object detection must look for so these checks can run."""
    labels: set[str] = set()
    for spec in rows:
        if spec.get("fn") == "solar":
            labels.add("solar panel")
        if spec.get("fn") == "vehicles" or spec.get("fact") == "vehicles_detected":
            labels.add("car")
    return labels


def run_checks(
    project_ids: set[str],
    roof: RoofRecord | None,
    interior: InteriorRecord | None,
    building: Building,
    tolerance: float,
    path: Path,
    check_ids: tuple[str, ...] = (),
) -> list[LayerCheck]:
    """The checks named by id when given; otherwise every check the table
    gives these projects. In table order."""
    facts = site_facts(roof, interior, building)
    out: list[LayerCheck] = []
    for spec in load_checks(path):
        if check_ids:
            if spec["id"] not in check_ids:
                continue
        else:
            targets = set(spec["projects"])
            if "*" not in targets and not targets & project_ids:
                continue
        if spec["kind"] == "fact":
            check = _fact_check(spec, facts)
        elif roof is None:
            check = None
        elif spec["fn"] == "footprint":
            check = measure.footprint_check(roof, interior, tolerance)
        elif spec["fn"] == "record":
            check = measure.record_check(roof, building, tolerance)
        elif spec["fn"] == "solar":
            check = measure.solar_check(roof)
        elif spec["fn"] == "vehicles":
            check = measure.vehicle_check(roof)
        else:
            check = None
        if check is not None:
            out.append(check)
    return out
