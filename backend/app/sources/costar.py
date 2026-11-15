"""CoStar commercial property records from the hackathon data kit.

The kit is a folder of static files, not an API (its openapi.yaml has no
paths). Read on 2026-11-14 from `costar-hackathon-kit/index.md` and
`models/commercial-property.md`:
- data/commercial-property.jsonl: 3,339 properties on CoStar's Matterport
  capture list, 1,758 in the Houston research market (researchMarketId 14)
  and 1,581 in Los Angeles (20). Most (2,765) are apartment communities.
- Fields used: id, buildingName, primaryAddress {streetNumber,
  streetDirection, streetName, streetType, city, state, latitude,
  longitude}, propertyType and propertySubtype (integer enums decoded with
  schemas/enums.json), buildingClass, constructionStatus,
  grossBuildingAreaSquareFeet, numStoriesAboveGrade, constructionEvents
  [{eventType, year}] (eventType 2 is ConstructionComplete), parking
  [{parkingType, totalSpaces}], primaryPhoto.attachmentUri.
- commercial-properties.csv: PropertyId, MatterportModelId, Address, City,
  State; one row per property and scan.
- data/commercial-listing-summary.jsonl (read 2026-11-15, models/commercial-
  listing-summary.md): 1,022 listings, every propertyId joins a property.
  Kept: listingStatus 1 (Active, on the market now) with a lease listingType
  (ListingType 14, 16-21) and forLeaseSummary {totalAvailableSquareFeet,
  maxContiguousSquareFeet, spaceCount}. The kit's note: available is not
  vacant; it is space being marketed, possibly still occupied.
Emails and phone numbers in the kit are fake by design; contacts are not
read here at all. The kit is licensed data and is not committed.
"""

from __future__ import annotations

import csv
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.geo import County

log = logging.getLogger(__name__)

SOURCE_LABEL = "CoStar commercial property records"
CONSTRUCTION_COMPLETE = 2
RESEARCH_MARKET_NAMES = {14: "Houston", 20: "Los Angeles"}


def available(kit_dir: Path) -> bool:
    return (kit_dir / "data" / "commercial-property.jsonl").exists()


def _enum(enums: dict[str, Any], name: str, value: Any) -> str | None:
    label = (enums.get(name) or {}).get(str(value))
    return None if label in (None, "Unknown", "PropertyTypeUnknown") else label


def _address(a: dict[str, Any]) -> str | None:
    street = " ".join(
        str(a[k])
        for k in ("streetNumber", "streetDirection", "streetName", "streetType")
        if a.get(k)
    )
    parts = [p for p in (street, a.get("city"), a.get("state")) if p]
    return ", ".join(parts) or None


def _record(raw: dict[str, Any], enums: dict[str, Any], scans: dict[int, list[str]]) -> dict | None:
    a = raw.get("primaryAddress") or {}
    lat, lon = a.get("latitude"), a.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    years = [
        e["year"]
        for e in raw.get("constructionEvents") or []
        if e.get("eventType") == CONSTRUCTION_COMPLETE and isinstance(e.get("year"), int)
    ]
    spaces = [
        p["totalSpaces"] for p in raw.get("parking") or [] if isinstance(p.get("totalSpaces"), int)
    ]
    address = _address(a)
    return {
        "id": raw["id"],
        "name": (raw.get("buildingName") or "").strip() or address or f"Property {raw['id']}",
        "address": address,
        "lat": float(lat),
        "lon": float(lon),
        "market": RESEARCH_MARKET_NAMES.get(a.get("researchMarketId")),
        "property_type": _enum(enums, "PropertyType", raw.get("propertyType")),
        "property_subtype": _enum(enums, "PropertySubtype", raw.get("propertySubtype")),
        "building_class": _enum(enums, "BuildingClass", raw.get("buildingClass")),
        "construction_status": _enum(enums, "ConstructionStatus", raw.get("constructionStatus")),
        "gross_sqft": raw.get("grossBuildingAreaSquareFeet"),
        "stories": raw.get("numStoriesAboveGrade"),
        "year_built": min(years) if years else None,
        "parking_spaces": sum(spaces) if spaces else None,
        "matterport_ids": scans.get(raw["id"], []),
        "photo_url": (raw.get("primaryPhoto") or {}).get("attachmentUri"),
    }


@lru_cache(maxsize=2)
def load_properties(kit_dir: Path) -> tuple[dict[str, Any], ...]:
    """Every commercial property in the kit with coordinates, in our field
    names. Loaded once per process (about 3,300 records)."""
    enums = json.loads((kit_dir / "schemas" / "enums.json").read_text(encoding="utf-8"))
    scans: dict[int, list[str]] = {}
    with (kit_dir / "commercial-properties.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            model = (row.get("MatterportModelId") or "").strip()
            if model:
                scans.setdefault(int(row["PropertyId"]), []).append(model)
    records = []
    with (kit_dir / "data" / "commercial-property.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = _record(json.loads(line), enums, scans)
                if record is not None:
                    records.append(record)
    log.info("CoStar kit: %d properties loaded", len(records))
    return tuple(records)


# ListingType values for space offered for lease (schemas/enums.json).
LEASE_TYPES = {
    14: "direct lease",
    16: "direct lease (building park)",
    17: "direct lease (shopping center)",
    18: "land for lease",
    19: "sublease",
    20: "lease assignment",
    21: "coworking",
}
LISTING_ACTIVE = 1


@lru_cache(maxsize=2)
def lease_listings(kit_dir: Path) -> dict[int, dict[str, Any]]:
    """Space on the market for lease now, by property id: square feet marketed
    (summed over the property's active lease listings), the largest contiguous
    block, the number of spaces and the listing types. Empty without the file."""
    path = kit_dir / "data" / "commercial-listing-summary.jsonl"
    if not path.exists():
        return {}
    out: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            summary = raw.get("forLeaseSummary") or {}
            kind = LEASE_TYPES.get(raw.get("listingType"))
            pid = raw.get("propertyId")
            if raw.get("listingStatus") != LISTING_ACTIVE or kind is None or not summary:
                continue
            if not isinstance(pid, int):
                continue
            entry = out.setdefault(
                pid, {"available_sqft": 0.0, "contiguous_sqft": 0.0, "spaces": 0, "types": []}
            )
            entry["available_sqft"] += float(summary.get("totalAvailableSquareFeet") or 0)
            entry["contiguous_sqft"] = max(
                entry["contiguous_sqft"], float(summary.get("maxContiguousSquareFeet") or 0)
            )
            entry["spaces"] += int(summary.get("spaceCount") or 0)
            if kind not in entry["types"]:
                entry["types"].append(kind)
    return {k: v for k, v in out.items() if v["available_sqft"] > 0 or v["contiguous_sqft"] > 0}


def properties_in_county(kit_dir: Path, county: County) -> list[dict[str, Any]]:
    records = load_properties(kit_dir)
    inside = county.contains_points([r["lon"] for r in records], [r["lat"] for r in records])
    return [r for r, keep in zip(records, inside, strict=True) if keep]
