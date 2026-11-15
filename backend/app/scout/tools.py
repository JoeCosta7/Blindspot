"""The actions the scout agent may take, and the state they work on.

Each tool returns a short code-written text for the model and the timeline,
and appends steps and candidates to `state.pending` for the stream. None of
them raises: a failed layer becomes an error step and a source_status entry.
Code, not the model, writes every number, check, estimate and score here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from shapely import wkt as shapely_wkt

from app import estimates, funding, matching
from app.config import Settings
from app.estimates import format_number
from app.geo import County
from app.models import (
    Asset,
    Estimate,
    FundingMatch,
    GeoOverlay,
    ImageOption,
    LayerCheck,
    Need,
    Observation,
    ScanRoom,
    ScoutEvent,
    ScoutStep,
    ScoutStepKind,
    SiteCandidate,
    SiteStatus,
    SourceStatus,
)
from app.scout import checks as site_checks
from app.scout import measure
from app.scout.provider import (
    Building,
    BuildingData,
    InteriorRecord,
    RoofRecord,
    ScanIndex,
    SitePicture,
)
from app.sources import SourceError, pvwatts

log = logging.getLogger(__name__)

TOOLS = (
    "search_properties",
    "search_public_buildings",
    "search_scans",
    "search_imagery",
    "inspect_roof",
    "inspect_interior",
    "evaluate_site",
    "finish",
)
# Project types that need a scanned interior to be judged at all.
INTERIOR_SOLUTIONS = {"cooling_center"}
# Projects sized from the measured roof (Vexcel footprint less rooftop equipment).
MEASURED_ROOF_PROJECTS = {"rooftop_solar_commercial", "resilience_hub"}
# Project types that need a flat roof with no solar on it.
ROOF_SOLUTIONS = {"rooftop_solar_commercial"}
DEFAULT_KEYWORDS = (
    "warehouse",
    "office",
    "retail",
    "store",
    "church",
    "school",
    "gym",
    "fitness",
    "community",
    "center",
    "clubhouse",
    "library",
    "hall",
)


@dataclass
class Inspection:
    building: Building
    roof: RoofRecord | None = None
    roof_checked: bool = False
    roof_error: str | None = None
    interior: InteriorRecord | None = None
    interior_checked: bool = False
    interior_error: str | None = None
    picture: SitePicture | None = None
    candidate: SiteCandidate | None = None
    # Everything the tools told the model about this building, for number checks.
    texts: list[str] = field(default_factory=list)


@dataclass
class ScoutState:
    county: County
    needs: list[Need]
    solar_yield: float | None
    solutions: tuple[matching.Solution, ...]
    programs: tuple[funding.FundingProgram, ...]
    provider: BuildingData
    settings: Settings
    client: httpx.AsyncClient
    energy_community: dict[str, Any] | None = None
    energy_community_checked: bool = False
    # The county report's schools, libraries and colleges (Geoapify).
    public_buildings: list[Asset] = field(default_factory=list)
    # The focus profile (agent.profile_for): what to search first and whether
    # the county owning the site should count in its favour.
    source_order: tuple[str, ...] = ("search_properties", "search_scans")
    property_types: list[str] = field(default_factory=list)
    prefer_public: bool = False
    # Site projects (profiles.Profile.land): candidates are land, judged for the
    # opportunity's own project type, starting from the report's own sites.
    land_mode: bool = False
    imagery_text: str | None = None
    report_sites: list[Asset] = field(default_factory=list)
    site_solution: matching.Solution | None = None
    public_kinds: tuple[str, ...] = ()
    check_ids: tuple[str, ...] = ()
    focus_hosts: str = ""
    judge_interior: bool = False
    seen: dict[str, Inspection] = field(default_factory=dict)
    steps: list[ScoutStep] = field(default_factory=list)
    pending: list[ScoutEvent] = field(default_factory=list)
    source_status: list[SourceStatus] = field(default_factory=list)
    actions_used: int = 0
    roofs_used: int = 0
    empty_searches: int = 0
    last_result: str = ""

    def shortlisted(self) -> int:
        return sum(
            1 for i in self.seen.values() if i.candidate and i.candidate.status == "shortlisted"
        )

    def all_text(self) -> str:
        return " ".join(t for i in self.seen.values() for t in i.texts)


def add_step(
    state: ScoutState,
    kind: ScoutStepKind,
    text: str,
    tool: str | None = None,
    building: Building | None = None,
) -> None:
    step = ScoutStep(
        index=len(state.steps),
        kind=kind,
        text=text,
        tool=tool,
        building_id=building.id if building else None,
        lat=building.lat if building else None,
        lon=building.lon if building else None,
    )
    state.steps.append(step)
    state.pending.append(ScoutEvent(type="step", step=step))


def _status(state: ScoutState, source: str, ok: bool, message: str | None = None) -> None:
    if not any(s.source == source for s in state.source_status):
        state.source_status.append(
            SourceStatus(source=source, ok=ok, cached=False, message=message)
        )


def _record(state: ScoutState, inspection: Inspection, text: str) -> str:
    inspection.texts.append(text)
    state.last_result = text
    return text


def _m2(value: float | None) -> str:
    return f"{format_number(round(value))} m²" if value is not None else "unknown"


def _building_line(b: Building) -> str:
    parts = [b.id, b.name]
    if b.address:
        parts.append(b.address)
    if b.property_type:
        kind = b.property_subtype or b.property_type
        parts.append(f"record type: {kind.replace('_', ' ')}")
    if b.gross_sqft:
        parts.append(f"{format_number(round(b.gross_sqft))} sq ft gross")
    if b.year_built:
        parts.append(f"built {b.year_built}")
    if b.parking_spaces is not None:
        parts.append(f"{b.parking_spaces} parking spaces")
    if b.building_class:
        parts.append(f"class {b.building_class}")
    if b.floors is not None:
        parts.append(f"{b.floors} floor(s)")
    if b.rooms is not None:
        parts.append(f"{b.rooms} room(s)")
    if b.room_labels:
        parts.append("rooms: " + ", ".join(b.room_labels[:6]))
    if b.building_type:
        parts.append(f"type: {b.building_type}")
    parts.append("has 3D scan" if b.scan_id else "no 3D scan")
    return " | ".join(parts)


DIRECTION_LABELS = {
    "oblique-south": "From the south",
    "oblique-north": "From the north",
    "oblique-east": "From the east",
    "oblique-west": "From the west",
}
PUBLIC_KINDS = ("school", "library", "college")


def _image_options(picture: SitePicture | None) -> list[ImageOption]:
    if picture is None:
        return []
    return [
        ImageOption(label=DIRECTION_LABELS.get(d, d), url=f"/api/scout/oblique/{picture.base}/{d}")
        for d in picture.directions
    ]


# --- searches ---------------------------------------------------------------------


SAME_PLACE_M = 40.0


def _same_place(a: Building, b: Building) -> bool:
    """The same building reached through two sources (a CoStar record and its
    scan, a school and a scan of it): within about 40 m, or the same name."""
    dy = (a.lat - b.lat) * 111_320
    dx = (a.lon - b.lon) * 111_320 * 0.87  # cos(30°), close enough for 40 m
    same_name = a.name.strip().lower() == b.name.strip().lower() and len(a.name) > 3
    return dx * dx + dy * dy < SAME_PLACE_M**2 or same_name


def admit(state: ScoutState, found: list[Building]) -> list[Building]:
    """New candidates only: a building already seen through another source is
    dropped, and lends its 3D scan to the one kept if that one had none."""
    kept: list[Building] = []
    for b in found:
        if b.id in state.seen:
            continue
        twin = next((i for i in state.seen.values() if _same_place(i.building, b)), None)
        if twin is None:
            twin_new = next((k for k in kept if _same_place(k, b)), None)
            if twin_new is None:
                kept.append(b)
            elif b.scan_id and not twin_new.scan_id:
                kept[kept.index(twin_new)] = replace(twin_new, scan_id=b.scan_id)
            continue
        if b.scan_id and not twin.building.scan_id and not twin.interior_checked:
            twin.building = replace(twin.building, scan_id=b.scan_id)
    return kept


async def search_report_sites(state: ScoutState, limit: int) -> str:
    """The sites the county report found for this project (its landfills,
    brownfields, plants or dams), the opportunity's own sites first."""
    picked: list[Building] = []
    for a in state.report_sites:
        bid = f"rs:{a.id}"
        if bid in state.seen:
            continue
        picked.append(
            Building(
                id=bid,
                name=a.name,
                lat=a.lat,
                lon=a.lon,
                found_by="report",
                address=str(a.attributes.get("city")) if a.attributes.get("city") else None,
                building_type=a.type,
                report_asset=a,
            )
        )
        if len(picked) >= limit:
            break
    picked = admit(state, picked)
    for b in picked:
        state.seen[b.id] = Inspection(building=b)
        state.seen[b.id].texts.append(_building_line(b))
    add_step(
        state,
        "observation",
        f"The county report lists {len(state.report_sites)} {state.focus_hosts}: "
        f"{len(picked)} new candidate(s).",
        "search_report_sites",
    )
    if not picked:
        state.empty_searches += 1
        state.last_result = "No new report sites. Use search_imagery to find more."
        return state.last_result
    state.empty_searches = 0
    state.last_result = "Sites from the county report:\n" + "\n".join(
        _building_line(b) for b in picked
    )
    return state.last_result


async def search_public_buildings(state: ScoutState, kinds: list[str], limit: int) -> str:
    """Schools, libraries and colleges from the county report, as candidates.
    A nearby 3D scan is linked when the scan index is available."""
    own_hosts = list(state.public_kinds) or (
        state.site_solution.hosts if state.site_solution else []
    )
    hosts = [h for h in own_hosts if h in PUBLIC_KINDS]
    asked = [k.strip().lower() for k in kinds if k.strip().lower() in PUBLIC_KINDS]
    # Only the kinds the project can be hosted at, in its catalogue order
    # (a weatherization clinic: libraries before schools).
    wanted = [k for k in asked if not hosts or k in hosts] or hosts or list(PUBLIC_KINDS)
    # Take the kinds in turn so one common kind (265 schools in Harris County)
    # cannot fill every slot. A library named for a school is the school's own.
    by_kind = {
        kind: [
            a
            for a in state.public_buildings
            if a.type == kind and not (kind == "library" and "school" in a.name.lower())
        ]
        for kind in wanted
    }
    pool = [
        group[i]
        for i in range(max((len(g) for g in by_kind.values()), default=0))
        for group in by_kind.values()
        if i < len(group)
    ]
    if not pool:
        add_step(
            state,
            "observation",
            "The county report lists no public buildings of that kind.",
            "search_public_buildings",
        )
        state.empty_searches += 1
        state.last_result = "No public buildings of that kind are in the county report."
        return state.last_result
    scans = ScanIndex()
    try:
        scans = ScanIndex(await state.provider.scanned_buildings(state.county))
    except Exception:  # linking a scan is a bonus
        pass
    picked: list[Building] = []
    for a in pool:
        bid = f"pb:{a.id}"
        if bid in state.seen:
            continue
        scan = scans.near(None, a.lat, a.lon)
        picked.append(
            Building(
                id=bid,
                name=a.name,
                lat=a.lat,
                lon=a.lon,
                found_by="geoapify",
                address=str(a.attributes.get("city")) if a.attributes.get("city") else None,
                scan_id=scan.scan_id if scan else None,
                floors=scan.floors if scan else None,
                rooms=scan.rooms if scan else None,
                room_labels=scan.room_labels if scan else (),
                building_type=a.type,
                report_asset=a,
            )
        )
        if len(picked) >= limit:
            break
    picked = admit(state, picked)
    for b in picked:
        state.seen[b.id] = Inspection(building=b)
        state.seen[b.id].texts.append(_building_line(b))
    add_step(
        state,
        "observation",
        f"Searched the county's {len(pool)} public buildings ({', '.join(wanted)}): "
        f"{len(picked)} new candidate(s).",
        "search_public_buildings",
    )
    if not picked:
        state.empty_searches += 1
        state.last_result = "No new public buildings of that kind."
        return state.last_result
    state.empty_searches = 0
    state.last_result = "Public buildings found:\n" + "\n".join(_building_line(b) for b in picked)
    return state.last_result


async def search_properties(
    state: ScoutState,
    property_types: list[str],
    min_sqft: int | None,
    max_stories: int | None,
    limit: int,
) -> str:
    """Commercial property records in the county (the CoStar layer) matching
    a type word (Industrial, Warehouse, Retail, Office, Flex...), a minimum
    gross area and a maximum number of storeys. Largest first."""
    try:
        buildings = await state.provider.properties(state.county)
    except Exception as exc:  # a broken kit file must not take the run down
        _status(state, "costar", False, str(exc)[:160])
        add_step(
            state, "error", f"The property records could not be read: {exc}", "search_properties"
        )
        state.last_result = f"search_properties failed: {exc}"
        return state.last_result
    if not buildings:
        _status(state, "costar", False, "No CoStar property records for this county")
        add_step(
            state, "observation", "No property records cover this county.", "search_properties"
        )
        state.last_result = (
            "No property records are available here; use search_scans or search_imagery."
        )
        state.empty_searches += 1
        return state.last_result
    _status(state, "costar", True)
    words = [w.strip().lower() for w in property_types if w.strip()]

    def matches(b: Building) -> bool:
        kind = f"{b.property_type or ''} {b.property_subtype or ''}".lower()
        if words and not any(w in kind for w in words):
            return False
        if min_sqft and (b.gross_sqft or 0) < min_sqft:
            return False
        return not (max_stories and (b.stories or 1) > max_stories)

    fresh = [b for b in buildings if b.id not in state.seen and matches(b)]
    # Buildings with a 3D scan first: a third layer, and the only way to judge an interior.
    fresh.sort(key=lambda b: (-(b.gross_sqft or 0), b.name))
    picked = fresh[:limit]
    picked = admit(state, picked)
    for b in picked:
        state.seen[b.id] = Inspection(building=b)
        state.seen[b.id].texts.append(_building_line(b))
    filters = ", ".join(words) if words else "any type"
    add_step(
        state,
        "observation",
        f"Searched {len(buildings):,} property records in the county for {filters}"
        f"{f', at least {min_sqft:,} sq ft' if min_sqft else ''}"
        f"{f', up to {max_stories} storeys' if max_stories else ''}: "
        f"{len(picked)} new candidate(s).",
        "search_properties",
    )
    if not picked:
        state.empty_searches += 1
        state.last_result = (
            "No new property records matched. Loosen the filters or try another tool."
        )
        return state.last_result
    state.empty_searches = 0
    state.last_result = "Property records found:\n" + "\n".join(_building_line(b) for b in picked)
    return state.last_result


async def search_scans(state: ScoutState, keywords: list[str], limit: int) -> str:
    """Scanned buildings in the county whose name or room labels match."""
    try:
        buildings = await state.provider.scanned_buildings(state.county)
        _status(state, "matterport", True)
    except SourceError as exc:
        _status(state, "matterport", False, str(exc))
        add_step(state, "error", f"The 3D scan index could not be loaded: {exc}", "search_scans")
        state.last_result = f"search_scans failed: {exc}"
        return state.last_result
    words = [w.strip().lower() for w in keywords if w.strip()] or list(DEFAULT_KEYWORDS)

    def matches(b: Building) -> bool:
        text = " ".join([b.name, *b.room_labels]).lower()
        return any(w in text for w in words)

    fresh = [b for b in buildings if b.id not in state.seen and matches(b)]
    fresh.sort(key=lambda b: (-(b.rooms or 0), -(b.floors or 0), b.name))
    picked = fresh[:limit]
    picked = admit(state, picked)
    for b in picked:
        state.seen[b.id] = Inspection(building=b)
    add_step(
        state,
        "observation",
        f"Searched {len(buildings):,} scanned buildings in the county for "
        f"{', '.join(words[:6])}: {len(picked)} new candidate(s).",
        "search_scans",
    )
    if not picked:
        state.empty_searches += 1
        state.last_result = (
            "No new scanned buildings matched those keywords. Try others or search_imagery."
        )
        return state.last_result
    state.empty_searches = 0
    lines = [_building_line(b) for b in picked]
    state.last_result = "Scanned buildings found:\n" + "\n".join(lines)
    for b in picked:
        state.seen[b.id].texts.append(_building_line(b))
    return state.last_result


async def search_imagery(state: ScoutState, text: str, near_id: str | None, limit: int) -> str:
    """Buildings found by searching the aerial imagery around a point."""
    anchor = state.seen.get(near_id or "")
    lat, lon = (
        (anchor.building.lat, anchor.building.lon)
        if anchor
        else (state.county.centroid_lat, state.county.centroid_lon)
    )
    where = f"around {anchor.building.name}" if anchor else "around the county centre"
    try:
        found = await state.provider.buildings_from_above(
            state.county,
            lat,
            lon,
            state.settings.scout_search_half_m,
            text,
            limit,
            need_structure=not state.land_mode,
        )
        _status(state, "vexcel", True)
    except SourceError as exc:
        _status(state, "vexcel", False, str(exc))
        add_step(state, "error", f"The imagery search failed: {exc}", "search_imagery")
        state.last_result = f"search_imagery failed: {exc}"
        return state.last_result
    picked = [b for b in found if b.id not in state.seen]
    picked = admit(state, picked)
    for b in picked:
        state.seen[b.id] = Inspection(building=b)
        state.seen[b.id].texts.append(_building_line(b))
    add_step(
        state,
        "observation",
        f'Searched the aerial imagery {where} for "{text}": {len(picked)} new building(s).',
        "search_imagery",
    )
    if not picked:
        state.empty_searches += 1
        state.last_result = "The imagery search found no new buildings there."
        return state.last_result
    state.empty_searches = 0
    state.last_result = "Buildings seen from above:\n" + "\n".join(
        _building_line(b) for b in picked
    )
    return state.last_result


# --- inspections ------------------------------------------------------------------------


def needed_labels(state: ScoutState) -> frozenset[str]:
    """Detection labels the run's project needs: its checks, plus rooftop
    equipment and panels when the estimate is sized from a measured roof."""
    ids = {state.site_solution.id} if state.site_solution else {s.id for s in state.solutions}
    rows = site_checks.selected(ids, state.check_ids, state.settings.data_dir / "site_checks.json")
    labels = site_checks.detection_labels(rows)
    if ids & MEASURED_ROOF_PROJECTS:
        labels |= {"rooftop hvac unit", "solar panel"}
    if ids & ROOF_SOLUTIONS:
        labels.add("solar panel")
    return frozenset(labels)


def _site_text(roof: RoofRecord) -> str:
    """What the aerial record says about a piece of land."""
    parts: list[str] = []
    if roof.parcel_wkt and roof.inside_parcel is not None:
        parts.append(f"parcel of {_m2(measure.wkt_area_m2(roof.parcel_wkt))}")
    else:
        parts.append("no parcel record here; the area around the point was used")
    if roof.structure_count:
        parts.append(f"{roof.structure_count} structure(s) on record")
    if roof.hardscape_m2 is not None:
        parts.append(f"paved area {_m2(roof.hardscape_m2)}")
    if roof.parcel_tree_cover_pct is not None:
        parts.append(f"tree cover {roof.parcel_tree_cover_pct:.0f}%")
    if not roof.detection_failed:
        cars = sum(1 for d in roof.detections if d.label == "car")
        parts.append(f"{cars} vehicle(s) detected")
    if roof.captured:
        parts.append(f"imagery {roof.captured}")
    return "; ".join(parts)


def _roof_text(roof: RoofRecord) -> str:
    hvac = measure.detections_on_roof(roof.detections, roof.footprint_wkt, "rooftop hvac unit")
    panels = measure.detections_on_roof(roof.detections, roof.footprint_wkt, "solar panel")
    cars = [d for d in roof.detections if d.label == "car"]
    covered = measure.obstruction_m2(hvac, roof.footprint_wkt)
    parts = [
        f"footprint {_m2(roof.footprint_m2)}",
        f"roof: {roof.roof_shape or 'shape unknown'}, {roof.roof_material or 'material unknown'}",
    ]
    if roof.roof_condition is not None:
        parts.append(f"condition {roof.roof_condition:.0f}/5")
    parts.append(
        "solar already on the roof (record)" if roof.solar_present else "no solar on record"
    )
    if roof.detection_failed and not roof.detection_failed.startswith("not run"):
        parts.append("live detection failed")
    else:
        parts.append(
            f"detected on the roof: {len(hvac)} HVAC unit(s) covering {_m2(covered)}, "
            f"{len(panels)} solar panel area(s)"
        )
        parts.append(f"{len(cars)} car(s) detected on the parcel")
    if roof.ac_units is not None:
        parts.append(f"{roof.ac_units} AC unit(s) on record")
    if roof.hardscape_m2 is not None:
        parts.append(f"paved area {_m2(roof.hardscape_m2)}")
    if roof.parcel_tree_cover_pct is not None:
        parts.append(f"parcel tree cover {roof.parcel_tree_cover_pct:.0f}%")
    if roof.building_type:
        parts.append(f"building type {roof.building_type}")
    if roof.captured:
        parts.append(f"imagery {roof.captured}")
    if roof.inside_parcel is False:
        parts.append("WARNING: the point fell outside the parcel returned")
    return "; ".join(parts)


async def inspect_roof(state: ScoutState, building_id: str) -> str:
    insp = state.seen[building_id]
    b = insp.building
    if insp.roof_checked:
        return _record(state, insp, f"{b.id} roof already inspected: {insp.texts[-1]}")
    extra = 3 if state.judge_interior and b.scan_id else 0
    if state.roofs_used >= state.settings.scout_max_roofs + extra:
        return _record(state, insp, "The roof inspection budget is spent. Evaluate or finish.")
    state.roofs_used += 1
    insp.roof_checked = True
    add_step(
        state,
        "action",
        f"Inspecting {b.name} from the air"
        if state.land_mode
        else f"Inspecting the roof and parcel of {b.name} from the air",
        "inspect_roof",
        b,
    )
    try:
        insp.roof = await state.provider.roof(
            b, allow_no_parcel=state.land_mode, labels=needed_labels(state)
        )
        _status(state, "vexcel", True)
    except SourceError as exc:
        insp.roof_error = str(exc)
        _status(state, "vexcel", False, str(exc))
        add_step(state, "error", f"The aerial record for {b.name} failed: {exc}", "inspect_roof", b)
        return _record(state, insp, f"{b.id}: aerial record unavailable ({exc})")
    if insp.roof is None:
        add_step(
            state, "observation", f"No parcel record at {b.name}'s location.", "inspect_roof", b
        )
        return _record(state, insp, f"{b.id}: no parcel or building footprint at this point")
    text = _site_text(insp.roof) if state.land_mode else _roof_text(insp.roof)
    add_step(state, "observation", f"{b.name}: {text}", "inspect_roof", b)
    return _record(state, insp, f"{b.id}: {text}")


def _interior_text(interior: InteriorRecord) -> str:
    floors = [a for _, a in interior.floors if a]
    parts = [f"3D scan of {len(interior.floors)} floor(s)"]
    if floors:
        parts.append(f"largest scanned floor {_m2(max(floors))}")
    else:
        parts.append("no floor area in the scan")
    labels = [label for label, _ in interior.rooms if label]
    if labels:
        parts.append("rooms: " + ", ".join(dict.fromkeys(labels))[:160])
    if interior.classification:
        parts.append(f"classified as {interior.classification.replace('_', ' ')}")
    if interior.summary:
        parts.append(f'scan summary: "{interior.summary.strip()}"')
    return "; ".join(parts)


async def inspect_interior(state: ScoutState, building_id: str) -> str:
    insp = state.seen[building_id]
    b = insp.building
    if insp.interior_checked:
        return _record(state, insp, f"{b.id} interior already inspected: {insp.texts[-1]}")
    insp.interior_checked = True
    if not b.scan_id:
        add_step(
            state,
            "observation",
            f"{b.name} has no 3D scan; the interior is unknown.",
            "inspect_interior",
            b,
        )
        return _record(state, insp, f"{b.id}: no 3D scan, interior unknown")
    add_step(state, "action", f"Walking through the 3D scan of {b.name}", "inspect_interior", b)
    try:
        insp.interior = await state.provider.interior(b)
        _status(state, "matterport", True)
    except SourceError as exc:
        insp.interior_error = str(exc)
        _status(state, "matterport", False, str(exc))
        add_step(
            state, "error", f"The scan of {b.name} could not be read: {exc}", "inspect_interior", b
        )
        return _record(state, insp, f"{b.id}: scan unavailable ({exc})")
    if insp.interior is None:
        return _record(state, insp, f"{b.id}: the scan returned no data")
    text = _interior_text(insp.interior)
    add_step(state, "observation", f"{b.name}: {text}", "inspect_interior", b)
    return _record(state, insp, f"{b.id}: {text}")


# --- evaluation -------------------------------------------------------------------------


def need_applies(solution_id: str, need: Need) -> bool:
    """A hazard need counts only for the hazard the project answers."""
    if need.id != "hazard_risk":
        return True
    detail = need.detail or ""

    def high(hazard: str) -> bool:
        return any(f"{hazard}: {level}" in detail for level in ("Very High", "Relatively High"))

    if solution_id == "cooling_center":
        return high("Heat wave")
    if solution_id == "parking_canopy_solar":
        return high("Heat wave") or high("Inland flooding") or high("Coastal flooding")
    return True


def _observations(
    roof: RoofRecord | None, interior: InteriorRecord | None, building: Building
) -> list[Observation]:
    out: list[Observation] = []
    if building.costar_id is not None:
        cs: list[tuple[str, Any, str | None]] = [
            ("Property type (record)", building.property_subtype or building.property_type, None),
            ("Gross building area", building.gross_sqft, "sq ft"),
            ("Storeys", building.stories, None),
            ("Year built", building.year_built, None),
            ("Parking spaces (record)", building.parking_spaces, None),
            ("Building class", building.building_class, None),
        ]
        for label, value, unit in cs:
            if value is None:
                continue
            out.append(
                Observation(
                    label=label,
                    value=round(value) if isinstance(value, float) else value,
                    unit=unit,
                    layer="costar",
                )
            )
    if roof is not None:
        when = roof.captured
        vx = [
            ("Building footprint", roof.footprint_m2, "m²", False),
            ("Roof shape", roof.roof_shape, None, False),
            ("Roof material", roof.roof_material, None, False),
            ("Roof condition (1-5)", roof.roof_condition, None, False),
            ("Solar on roof (record)", roof.solar_present, None, False),
            ("AC units on roof (record)", roof.ac_units, None, False),
            ("Paved area on parcel", roof.hardscape_m2, "m²", False),
            ("Parcel tree cover", roof.parcel_tree_cover_pct, "%", False),
            ("Building type (record)", roof.building_type, None, False),
        ]
        if not roof.detection_failed:
            hvac = measure.detections_on_roof(
                roof.detections, roof.footprint_wkt, "rooftop hvac unit"
            )
            panels = measure.detections_on_roof(roof.detections, roof.footprint_wkt, "solar panel")
            cars = [d for d in roof.detections if d.label == "car"]
            vx += [
                ("Rooftop HVAC units detected", len(hvac), None, True),
                ("Solar panel areas detected", len(panels), None, True),
                ("Cars detected on parcel", len(cars), None, True),
            ]
        for label, value, unit, by_model in vx:
            if value is None:
                continue
            out.append(
                Observation(
                    label=label,
                    value=round(value, 1) if isinstance(value, float) else value,
                    unit=unit,
                    layer="vexcel",
                    captured=when,
                    by_model=by_model,
                )
            )
    if interior is not None:
        floors = [a for _, a in interior.floors if a]
        mp = [
            ("Scanned floors", len(interior.floors), None),
            ("Largest scanned floor", max(floors) if floors else None, "m²"),
            ("Scanned rooms", len(interior.rooms), None),
            ("Scan classification", interior.classification, None),
        ]
        for label, value, unit in mp:
            if value is None:
                continue
            out.append(
                Observation(
                    label=label,
                    value=round(value, 1) if isinstance(value, float) else value,
                    unit=unit,
                    layer="matterport",
                    by_model=label == "Scan classification",
                )
            )
    return out


def _overlays(roof: RoofRecord | None) -> list[GeoOverlay]:
    if roof is None:
        return []
    names = {"rooftop hvac unit": "Rooftop HVAC unit", "solar panel": "Solar panel", "car": "Car"}
    items: list[tuple[str, str | None]] = [
        ("Parcel", roof.parcel_wkt),
        ("Footprint", roof.footprint_wkt),
    ]
    items += [(names.get(d.label, d.label), d.wkt) for d in roof.detections]
    overlays = []
    for label, text in items:
        if not text:
            continue
        geom = shapely_wkt.loads(text)
        polygons = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polygons:
            if poly.geom_type == "Polygon":
                overlays.append(
                    GeoOverlay(label=label, coordinates=[(x, y) for x, y in poly.exterior.coords])
                )
    return overlays


async def _site_yield(state: ScoutState, b: Building) -> tuple[float | None, bool]:
    try:
        value, _ = await pvwatts.fetch_yield(state.client, state.settings, b.lat, b.lon)
        return value, True
    except SourceError:
        return state.solar_yield, False
    except Exception as exc:  # never fail the evaluation over the yield
        log.warning("pvwatts for %s failed: %r", b.id, exc)
        return state.solar_yield, False


async def fetch_photos(state: ScoutState, candidates: list[SiteCandidate]) -> list[SiteCandidate]:
    """Photos for the given candidates only, fetched together. A failed photo
    leaves its candidate without one."""

    async def one(c: SiteCandidate) -> SiteCandidate:
        insp = state.seen.get(c.building_id)
        if insp is None or insp.roof is None:
            return c
        try:
            insp.picture = await state.provider.image(insp.building, insp.roof)
        except Exception as exc:  # the photo is for the card; the site stands
            log.warning("scout image for %s failed: %r", c.building_id, exc)
            return c
        if insp.picture is None:
            return c
        return c.model_copy(
            update={
                "image_url": f"/api/scout/image/{insp.picture.name}",
                "image_view": insp.picture.view,
                "image_options": _image_options(insp.picture),
            }
        )

    return list(await asyncio.gather(*(one(c) for c in candidates)))


async def evaluate_site(
    state: ScoutState, building_id: str, solution_ids: list[str], note: str | None
) -> str:
    insp = state.seen[building_id]
    b = insp.building
    if not insp.roof_checked:
        return _record(state, insp, f"Inspect the roof of {b.id} before evaluating it.")
    if b.scan_id and not insp.interior_checked:
        await inspect_interior(state, building_id)
    roof, interior = insp.roof, insp.interior
    known = {s.id: s for s in state.solutions}
    wanted = [known[s] for s in dict.fromkeys(solution_ids) if s in known] or list(state.solutions)
    if state.site_solution is not None:
        # The opportunity's own project, not the generic building projects.
        wanted = [state.site_solution]

    kept: list[matching.Solution] = []
    need_ids: list[str] = []
    dropped: list[str] = []
    for solution in wanted:
        matched = [n for n in state.needs if solution.answers(n) and need_applies(solution.id, n)]
        if not matched and state.site_solution is not None:
            matched = list(state.needs)  # the opportunity's own needs, already scoped
        if not matched:
            dropped.append(f"{solution.id}: answers no flagged need")
            continue
        if solution.id in ROOF_SOLUTIONS:
            if roof is None or roof.footprint_m2 is None:
                dropped.append(f"{solution.id}: no measured footprint")
                continue
            if roof.solar_present or measure.detections_on_roof(
                roof.detections, roof.footprint_wkt, "solar panel"
            ):
                dropped.append(f"{solution.id}: the roof already has solar")
                continue
            if roof.roof_shape and "flat" not in roof.roof_shape.lower():
                dropped.append(f"{solution.id}: the roof is {roof.roof_shape}, not flat")
                continue
        # No scan does not rule a building out; the scan's checks are skipped instead.
        if solution.id == "parking_canopy_solar" and (roof is None or not roof.hardscape_m2):
            dropped.append(f"{solution.id}: no paved area on the parcel record")
            continue
        kept.append(solution)
        need_ids += [n.id for n in matched if n.id not in need_ids]

    kept_ids = {s.id for s in kept}
    # Which checks run depends on the project: data/site_checks.json.
    checks: list[LayerCheck] = site_checks.run_checks(
        kept_ids,
        roof,
        interior,
        b,
        state.settings.scout_footprint_tolerance,
        state.settings.data_dir / "site_checks.json",
        state.check_ids,
    )
    factor, reasons = measure.confidence_factor(
        roof, interior, checks, state.settings, land=state.land_mode
    )
    failed = [c for c in checks if not c.ok and not c.label.endswith("vs footprint")]
    if failed:
        factor *= state.settings.scout_factor_failed_check ** len(failed)
        reasons.append(f"{len(failed)} check(s) not met: " + ", ".join(c.label for c in failed))
    # The county can act on its own buildings directly; a private site needs the
    # owner as a partner, so it ranks a little lower (our own choice, config).
    if state.prefer_public and b.found_by != "geoapify":
        factor *= state.settings.scout_factor_private_site
        reasons.append("privately owned, so the county would need the owner as a partner")
    flags: list[str] = []
    flood = measure.flood_flag(state.needs, interior)
    if flood:
        flags.append(flood)
    if roof is not None and roof.roof_condition is not None and roof.roof_condition <= 2:
        flags.append(
            f"Roof condition is rated {roof.roof_condition:.0f} of 5 in the aerial record; "
            "re-roofing would come before any rooftop installation."
        )

    figures: list[Estimate] = []
    if kept and b.report_asset is not None:
        from app.pipeline import size_estimates

        site_yield, specific = await _site_yield(state, b)
        figures = size_estimates(kept[0].id, b.report_asset, site_yield, specific)
    if kept_ids & MEASURED_ROOF_PROJECTS and roof is not None and roof.footprint_m2:
        # A measured roof replaces any illustrative array size.
        hvac = measure.detections_on_roof(roof.detections, roof.footprint_wkt, "rooftop hvac unit")
        site_yield, specific = await _site_yield(state, b)
        figures = estimates.rooftop_solar(
            roof.footprint_m2,
            measure.obstruction_m2(hvac, roof.footprint_wkt),
            site_yield,
            specific,
        )

    leads: dict[str, FundingMatch] = {}
    for solution in kept:
        for lead in funding.match_funding(
            state.programs,
            solution.id,
            b.report_asset.type
            if b.report_asset
            else (None if state.land_mode else "commercial_building"),
            energy_community=state.energy_community,
            energy_community_checked=state.energy_community_checked,
        ):
            leads.setdefault(lead.program_id, lead)
    funding_leads = sorted(
        leads.values(), key=lambda f: (funding.STATUS_ORDER[f.status], f.deadline or "9999")
    )

    needs = [n for n in state.needs if n.id in need_ids]
    score = round(matching.need_score(needs) * factor) if needs else 0
    status: SiteStatus
    if not kept:
        status, reason = "rejected", "; ".join(dropped) or "no project type fits"
    elif factor < 0.75 or any(not c.ok and c.label.startswith("Scan floor") for c in checks):
        status, reason = "low_confidence", "; ".join(reasons) or "a cross-layer check failed"
    else:
        status, reason = (
            "shortlisted",
            ("all checks passed" if not failed else "some checks not met")
            if checks
            else "no checks apply",
        )
        if reasons:
            reason += "; " + "; ".join(reasons)

    agent_note = None
    if note and note.strip():
        from app.narrative import extract_numbers

        if extract_numbers(note) <= extract_numbers(state.all_text()):
            agent_note = note.strip()

    candidate = SiteCandidate(
        building_id=b.id,
        name=b.name,
        address=b.address,
        lat=b.lat,
        lon=b.lon,
        property_type=(roof.building_type if roof else None)
        or (interior.classification if interior else None),
        status=status,
        solution_ids=[s.id for s in kept],
        need_ids=need_ids,  # type: ignore[arg-type]
        layers_used=(["costar"] if b.costar_id is not None else [])
        + (["vexcel"] if roof else [])
        + (["matterport"] if interior else []),
        observations=_observations(roof, interior, b),
        checks=checks,
        estimates=figures,
        funding=funding_leads,
        flags=flags,
        status_reason=reason,
        agent_note=agent_note,
        score=max(0, min(100, score)),
        image_url=f"/api/scout/image/{insp.picture.name}" if insp.picture else None,
        image_view=insp.picture.view if insp.picture else None,
        image_options=_image_options(insp.picture),
        overlays=_overlays(roof),
        interior_summary=interior.summary if interior else None,
        scan_rooms=[
            ScanRoom(label=label, area_m2=area, ceiling_m=ceiling)
            for label, area, ceiling in (interior.room_list if interior else ())
        ][:12],
        scan_floors=len(interior.floors) if interior else None,
        scan_image_url=interior.image_url if interior else None,
        tour_url=interior.tour_url if interior else None,
    )
    insp.candidate = candidate
    state.pending.append(ScoutEvent(type="candidate", candidate=candidate))
    label = {
        "shortlisted": "Shortlisted",
        "low_confidence": "Kept with low confidence",
        "rejected": "Rejected",
    }[status]
    add_step(
        state,
        "decision",
        f"{label}: {b.name} (score {candidate.score}). {reason}",
        "evaluate_site",
        b,
    )
    return _record(
        state,
        insp,
        f"{b.id} evaluated: {status}, score {candidate.score}; projects: "
        f"{', '.join(s.id for s in kept) or 'none'}; {reason}",
    )
