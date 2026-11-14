"""analyze_county(): run every source concurrently, then needs -> matching ->
estimates -> funding -> narrative.

A failed source never fails the request. Each one runs under a timeout and
reports into `source_status`; the rest of the report is built from whatever
loaded.
"""

from __future__ import annotations

import asyncio
import logging
import string
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app import estimates, funding, matching, narrative, needs
from app.config import Settings
from app.geo import County
from app.models import (
    Asset,
    CountyContext,
    CountyReport,
    Estimate,
    Need,
    Opportunity,
    Resources,
    SourceStatus,
)
from app.sources import (
    SourceError,
    afdc,
    census,
    dams,
    farms,
    geoapify,
    lmop,
    nri,
    pvwatts,
    repowering,
    state_energy,
)

log = logging.getLogger(__name__)

# Human names for sources, used in caveats and by the UI loading list.
SOURCE_NAMES = {
    "census": "Census ACS (county)",
    "census_national": "Census ACS (national baseline)",
    "nri": "FEMA National Risk Index",
    "repowering": "EPA RE-Powering sites",
    "lmop": "EPA landfill database",
    "geoapify": "OpenStreetMap public buildings",
    "ev_stations": "EV charging stations",
    "ev_national_count": "National EV station count",
    "pvwatts": "PVWatts solar yield",
    "state_energy": "State electricity bills and grid carbon (EIA, EPA eGRID)",
    "dams": "ORNL non-powered dams",
    "farms": "USDA farm census and EPA digester database",
    "gemini": "Gemini narrative",
}

BUILDING_TYPES = ("school", "library", "college")
SOLAR_SOLUTIONS = ("community_solar", "resilience_hub")


async def _run(
    name: str,
    call: Callable[[], Awaitable[tuple[Any, bool]]],
    timeout_s: float,
    slot: asyncio.Semaphore | None = None,
) -> tuple[Any, SourceStatus]:
    """Run one source. Returns (value or None, status); never raises."""
    try:
        if slot is not None:
            async with slot:  # time spent queueing does not count against the timeout
                value, cached = await asyncio.wait_for(call(), timeout_s)
        else:
            value, cached = await asyncio.wait_for(call(), timeout_s)
        return value, SourceStatus(source=name, ok=True, cached=cached)
    except SourceError as exc:
        message = str(exc)
    except TimeoutError:
        message = f"Timed out after {timeout_s:.0f} s"
    except Exception as exc:  # a source bug must not take the report down
        log.exception("source %s failed unexpectedly", name)
        message = f"Unexpected error: {type(exc).__name__}"
    return None, SourceStatus(source=name, ok=False, cached=False, message=message)


def _local(read: Callable[[], Any]) -> Callable[[], Awaitable[tuple[Any, bool]]]:
    """Wrap a local-file lookup so it runs through `_run` like a network source."""

    async def call() -> tuple[Any, bool]:
        return read(), False

    return call


# --- rows -> assets -----------------------------------------------------------


def _display_name(name: Any, fallback: str) -> str:
    text = str(name).strip() if name else ""
    if not text:
        return fallback
    return string.capwords(text) if text.isupper() else text


def _rounded(value: Any, digits: int) -> float | None:
    return round(float(value), digits) if value is not None else None


def repowering_assets(rows: list[dict[str, Any]]) -> list[Asset]:
    return [
        Asset(
            id=f"repowering:{row['site_id']}",
            type=row["site_type"],
            name=_display_name(row.get("name"), "Unnamed site"),
            lat=row["lat"],
            lon=row["lon"],
            attributes={
                "acres": _rounded(row.get("acres"), 2),
                "program": _display_name(row.get("program"), "") or None,
                "city": _display_name(row.get("city"), "") or None,
                "pv_scale": row.get("pv_scale"),
                "wind_scale": row.get("wind_scale"),
                "epa_pv_mw": _rounded(row.get("epa_pv_mw"), 2),
                "epa_wind_mw": _rounded(row.get("epa_wind_mw"), 2),
                "transmission_miles": _rounded(row.get("transmission_miles"), 1),
                "profile_url": row.get("profile_url"),
            },
            source=repowering.SOURCE_LABEL,
        )
        for row in rows
    ]


def lmop_assets(rows: list[dict[str, Any]]) -> list[Asset]:
    """Landfills with coordinates. Those without cannot be mapped."""
    return [
        Asset(
            id=f"lmop:{row['landfill_id']}",
            type="lmop_landfill",
            name=_display_name(row.get("name"), "Unnamed landfill"),
            lat=row["lat"],
            lon=row["lon"],
            attributes={
                "landfill_status": row.get("status"),
                "project_status": row.get("project_status"),
                "is_candidate": bool(row.get("is_candidate")),
                "has_operational_project": bool(row.get("has_operational_project")),
                "operational_mw": _rounded(row.get("operational_mw"), 2),
                "waste_in_place_tons": row.get("waste_in_place_tons"),
                "lfg_collected_mmscfd": row.get("lfg_collected_mmscfd"),
                "lfg_collected_year": row.get("lfg_collected_year"),
                "lfg_generated_mmscfd": row.get("lfg_generated_mmscfd"),
                "ownership_type": row.get("ownership_type"),
            },
            source=lmop.SOURCE_LABEL,
        )
        for row in rows
        if row.get("lat") is not None and row.get("lon") is not None
    ]


def dam_assets(rows: list[dict[str, Any]]) -> list[Asset]:
    return [
        Asset(
            id=f"dam:{row['dam_id']}",
            type="non_powered_dam",
            name=_display_name(row.get("name"), "Unnamed dam"),
            lat=row["lat"],
            lon=row["lon"],
            attributes={
                "capacity_mw": row.get("capacity_mw"),
                "generation_mwh_yr": row.get("generation_mwh_yr"),
                "capacity_factor": row.get("capacity_factor"),
                "head_ft": row.get("head_ft"),
                "waterway": _display_name(row.get("waterway"), "") or None,
                "owner_type": row.get("owner_type"),
                "city": _display_name(row.get("city"), "") or None,
                "input_data": row.get("input_data"),
            },
            source=dams.SOURCE_LABEL,
        )
        for row in rows
    ]


def _inside(county: County, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mask = county.contains_points([r["lon"] for r in rows], [r["lat"] for r in rows])
    return [row for row, keep in zip(rows, mask, strict=True) if keep]


def building_assets(county: County, rows: list[dict[str, Any]]) -> list[Asset]:
    return [
        Asset(
            id=f"osm:{row['osm_id']}",
            type=row["type"],
            name=row["name"],
            lat=row["lat"],
            lon=row["lon"],
            attributes={"city": row.get("city")},
            source=geoapify.SOURCE_LABEL,
        )
        for row in _inside(county, rows)
    ]


def ev_assets(county: County, rows: list[dict[str, Any]]) -> list[Asset]:
    return [
        Asset(
            id=f"afdc:{row['id']}",
            type="ev_station",
            name=row["name"],
            lat=row["lat"],
            lon=row["lon"],
            attributes={
                "city": row.get("city"),
                "level2_ports": row.get("level2_ports"),
                "dc_fast_ports": row.get("dc_fast_ports"),
            },
            source=afdc.SOURCE_LABEL,
        )
        for row in _inside(county, rows)
    ]


def _cap_for_map(groups: list[tuple[list[Asset], int]], must_include: set[str]) -> list[Asset]:
    """Apply the per-group map limits, always keeping assets an opportunity
    refers to. Each group is already sorted best-first."""
    shown: list[Asset] = []
    for assets, limit in groups:
        kept = assets[:limit]
        kept_ids = {a.id for a in kept}
        kept += [a for a in assets[limit:] if a.id in must_include and a.id not in kept_ids]
        shown += kept
    return shown


# --- the pipeline ---------------------------------------------------------------


async def analyze_county(
    county: County, client: httpx.AsyncClient, settings: Settings
) -> CountyReport:
    timeout = settings.source_timeout_s
    data_dir = settings.data_dir
    centroid = (county.centroid_lon, county.centroid_lat)

    results = await asyncio.gather(
        _run(
            "census",
            lambda: census.fetch_county(client, settings, county.state_fips, county.county_code),
            timeout,
        ),
        _run("census_national", lambda: census.fetch_national(client, settings), timeout),
        _run("nri", _local(lambda: nri.ratings_for_county(data_dir, county.fips)), timeout),
        _run(
            "repowering",
            _local(lambda: repowering.sites_for_county(data_dir, county.fips)),
            timeout,
        ),
        _run("lmop", _local(lambda: lmop.landfills_for_county(data_dir, county.fips)), timeout),
        _run(
            "geoapify",
            lambda: geoapify.fetch_public_buildings(client, settings, county.fips, county.bounds),
            timeout,
        ),
        _run(
            "ev_stations",
            lambda: afdc.fetch_state_stations(client, settings, county.state),
            timeout,
        ),
        _run("ev_national_count", lambda: afdc.fetch_national_count(client, settings), timeout),
        _run(
            "pvwatts",
            lambda: pvwatts.fetch_yield(client, settings, county.centroid_lat, county.centroid_lon),
            timeout,
        ),
        _run(
            "state_energy",
            _local(lambda: state_energy.for_state(data_dir, county.state)),
            timeout,
        ),
        _run("dams", _local(lambda: dams.dams_for_county(data_dir, county.fips)), timeout),
        _run("farms", _local(lambda: farms.farms_for_county(data_dir, county.fips)), timeout),
    )
    (
        (acs, _),
        (acs_national, _),
        (nri_row, _),
        (site_rows, _),
        (landfill_rows, lmop_status),
        (building_rows, _),
        (station_rows, _),
        (national_stations, _),
        (centroid_yield, _),
        (energy, _),
        (dam_rows, _),
        (farm_row, _),
    ) = results
    statuses: list[SourceStatus] = [status for _, status in results]
    ok = {status.source: status.ok for status in statuses}

    # --- assets ---------------------------------------------------------------
    sites = repowering_assets(site_rows or [])
    sites.sort(key=lambda a: -(a.attributes.get("acres") or 0.0))  # type: ignore[operator]
    landfills = lmop_assets(landfill_rows or [])
    unmapped = len(landfill_rows or []) - len(landfills)
    if unmapped:
        lmop_status.message = (
            f"{unmapped} landfill(s) in the EPA file have no coordinates and are not mapped"
        )
    buildings = building_assets(county, building_rows or [])
    buildings.sort(key=lambda a: matching.distance(a, *centroid))
    stations = ev_assets(county, station_rows or [])
    dam_sites = dam_assets(dam_rows or [])
    dam_sites.sort(key=lambda a: -(a.attributes.get("capacity_mw") or 0.0))  # type: ignore[operator]
    all_assets = sites + landfills + buildings + stations + dam_sites

    # --- needs ----------------------------------------------------------------
    flagged: list[Need] = []
    if acs is not None and acs_national is not None:
        flagged += needs.acs_needs(acs, acs_national, settings)
    if ok["ev_stations"]:
        benchmark = needs.ev_benchmark_per_10k(
            national_stations, acs_national.population if acs_national else None, settings
        )
        ev = needs.ev_need(len(stations), acs.population if acs else None, benchmark, settings)
        if ev:
            flagged.append(ev)
    if ok["lmop"]:
        landfill = needs.landfill_need(landfill_rows or [], settings)
        if landfill:
            flagged.append(landfill)
    hazard = needs.hazard_need(nri_row, settings)
    if hazard:
        flagged.append(hazard)
    for extra in (
        needs.electricity_cost_need(energy, settings),
        needs.grid_carbon_need(energy, settings),
        needs.farm_waste_need(farm_row, settings),
    ):
        if extra:
            flagged.append(extra)
    flagged.sort(key=lambda n: (-n.severity, n.id))

    # --- matching -------------------------------------------------------------
    solutions = matching.load_solutions(data_dir / "solutions.json")
    matches = matching.match_opportunities(
        flagged,
        all_assets,
        solutions,
        centroid,
        settings.assets_per_solution,
        settings.max_opportunities,
    )

    # --- site-specific solar yield for the top sites ----------------------------
    solar_assets: dict[str, Asset] = {}
    for match in matches:
        if (
            match.asset is not None
            and match.solution.id in SOLAR_SOLUTIONS
            and len(solar_assets) < settings.max_pvwatts_sites
        ):
            solar_assets.setdefault(match.asset.id, match.asset)
    site_results = await asyncio.gather(
        *(
            _run(
                "pvwatts_site",
                lambda a=a: pvwatts.fetch_yield(client, settings, a.lat, a.lon),
                timeout,
            )
            for a in solar_assets.values()
        )
    )
    site_yields = {
        asset_id: value
        for asset_id, (value, _) in zip(solar_assets, site_results, strict=True)
        if value is not None
    }

    # --- estimates and funding -------------------------------------------------
    programs = funding.load_programs(data_dir / "funding_programs.json")
    opportunities: list[Opportunity] = []
    for match in matches:
        asset = match.asset
        site_yield = site_yields.get(asset.id) if asset else None
        solar_yield = site_yield if site_yield is not None else centroid_yield
        figures: list[Estimate]
        if asset is None:
            figures = []  # county-wide: no site to size
        elif match.solution.id == "community_solar":
            acres = asset.attributes.get("acres")
            figures = estimates.solar_on_land(
                float(acres) if isinstance(acres, (int, float)) else None,
                solar_yield,
                site_yield is not None,
            )
        elif match.solution.id == "resilience_hub":
            figures = estimates.resilience_hub(solar_yield, site_yield is not None)
        elif match.solution.id == "landfill_gas_energy":
            figures = estimates.landfill_gas(asset.attributes)
        elif match.solution.id == "small_hydro":
            figures = estimates.hydro_at_dam(asset.attributes)
        elif match.solution.id == "wind_on_reclaimed_land":
            figures = estimates.wind_on_land(asset.attributes)
        else:
            figures = []
        opportunities.append(
            Opportunity(
                id=match.id,
                solution_id=match.solution.id,
                title=match.title,
                asset_id=asset.id if asset else None,
                need_ids=[n.id for n in match.needs],
                score=match.score,
                estimates=figures,
                funding=funding.match_funding(
                    programs, match.solution.id, asset.type if asset else None
                ),
            )
        )

    # --- what the map gets, and what was looked for but not found ----------------
    counts = Counter(a.type for a in all_assets)
    searched: list[tuple[str, tuple[str, ...]]] = [
        ("repowering", repowering.SITE_TYPES),
        ("lmop", ("lmop_landfill",)),
        ("geoapify", BUILDING_TYPES),
        ("ev_stations", ("ev_station",)),
        ("dams", ("non_powered_dam",)),
    ]
    missing = [t for source, types in searched if ok[source] for t in types if counts[t] == 0]
    used = {o.asset_id for o in opportunities if o.asset_id}
    shown_assets = _cap_for_map(
        [
            (sites, settings.max_sites_per_county),
            (landfills, len(landfills)),
            (buildings, settings.max_buildings_per_county),
            (stations, settings.max_ev_markers_per_county),
            (dam_sites, len(dam_sites)),
        ],
        used,
    )

    # --- narrative --------------------------------------------------------------
    failed = [SOURCE_NAMES.get(s.source, s.source) for s in statuses if not s.ok]
    payload = narrative.build_payload(county.name, county.state, flagged, opportunities)
    brief, gemini_status = await narrative.write_narrative(payload, failed, settings)
    statuses.append(gemini_status)

    return CountyReport(
        fips=county.fips,
        name=county.name,
        state=county.state,
        population=acs.population if acs else None,
        context=CountyContext(
            median_household_income=acs.median_household_income if acs else None,
            poverty_rate=acs.poverty_rate if acs else None,
            unemployment_rate=acs.unemployment_rate if acs else None,
            electric_heat_share=acs.electric_heat_share if acs else None,
            state_electricity_price_cents_kwh=(
                energy["state"].get("residential_price_cents_kwh") if energy else None
            ),
            state_grid_co2_lb_per_mwh=(
                energy["state"].get("grid_co2_lb_per_mwh") if energy else None
            ),
        ),
        needs=flagged,
        assets=shown_assets,
        asset_counts=dict(counts),
        missing_asset_types=missing,  # type: ignore[arg-type]
        resources=Resources(
            solar_kwh_per_kw_year=round(centroid_yield) if centroid_yield else None,
            land_in_farms_acres=farm_row.get("land_in_farms_acres") if farm_row else None,
            cropland_acres=farm_row.get("cropland_acres") if farm_row else None,
        ),
        opportunities=opportunities,
        narrative=brief,
        source_status=statuses,
    )
