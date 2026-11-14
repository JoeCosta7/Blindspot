"""analyze_county(): the three parts of a report, in order.

1. Needs: every source runs concurrently, then needs.py flags the county's
   environmental and socioeconomic problems.
2. Assets and factors: the mapped sites, and factors.py's county-level facts
   (climate, land, buildings, workforce, economy, community, energy,
   infrastructure), which include everything profile.py measures.
3. Opportunities: Gemini writes them from 1 and 2 (opportunities.py). Code
   then adds the score, the estimates and the funding leads. If Gemini is
   unavailable or its answer is rejected, the rule matches from matching.py
   are served instead.

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
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app import (
    estimates,
    factors,
    funding,
    highlights,
    matching,
    narrative,
    needs,
    opportunities,
    peers,
    profile,
)
from app.config import Settings
from app.geo import County
from app.models import (
    Asset,
    CountyContext,
    CountyReport,
    Estimate,
    Need,
    Opportunity,
    PeerCounty,
    Resources,
    SourceStatus,
)
from app.sources import (
    SourceError,
    afdc,
    air_quality,
    attains,
    census,
    dams,
    drinking_water,
    earthengine,
    energy_burden,
    energy_communities,
    farms,
    geoapify,
    grantsgov,
    lmop,
    nri,
    openfema,
    openmeteo,
    places,
    power_plants,
    pvwatts,
    repowering,
    rucc,
    state_energy,
    svi,
    transit,
    uswtdb,
    utilities,
    wildfire,
)

log = logging.getLogger(__name__)

# Human names for sources, used in caveats and by the UI loading list.
SOURCE_NAMES = {
    "census": "Census ACS (county)",
    "census_national": "Census ACS (national baseline)",
    "nri": "FEMA National Risk Index",
    "repowering": "EPA RE-Powering sites",
    "lmop": "EPA landfill database",
    "geoapify": "Geoapify public buildings",
    "ev_stations": "EV charging stations",
    "ev_national_count": "National EV station count",
    "pvwatts": "PVWatts solar yield",
    "state_energy": "State electricity bills and grid carbon (EIA, EPA eGRID)",
    "dams": "ORNL non-powered dams",
    "farms": "USDA farm census and EPA digester database",
    "earthengine": "Urban heat and tree canopy (Google Earth Engine)",
    "svi": "CDC Social Vulnerability Index",
    "climate": "Climate history (Open-Meteo)",
    "peers": "Census ACS (all counties, for peer counties)",
    "energy_burden": "DOE LEAD energy burden",
    "energy_communities": "Treasury energy community lists",
    "power_plants": "EIA-860 power plants",
    "utilities": "EIA-861 electric utilities",
    "rucc": "USDA rural-urban codes",
    "places": "CDC PLACES health estimates",
    "air_quality": "EPA air quality design values",
    "wildfire": "USFS wildfire risk to communities",
    "disasters": "OpenFEMA disaster declarations",
    "drinking_water": "EPA ECHO drinking water systems",
    "waters": "EPA ATTAINS assessed waters",
    "wind_turbines": "US Wind Turbine Database",
    "transit": "BTS National Transit Map",
    "gemini": "Gemini opportunities and brief",
    "grants_gov": "Grants.gov live listings",
    # Site Scout (app/scout/)
    "vexcel": "Vexcel aerial imagery and property records",
    "matterport": "Matterport 3D scans",
    "scout_gemini": "Gemini scout decisions",
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


def combined_status(name: str, parts: list[str], statuses: list[SourceStatus]) -> SourceStatus:
    """One status for a source that ran as several calls (one per part)."""
    failures = [
        f"{part}: {status.message}"
        for part, status in zip(parts, statuses, strict=True)
        if not status.ok
    ]
    return SourceStatus(
        source=name,
        ok=not failures,
        cached=all(status.cached for status in statuses),
        message=(
            f"{len(failures)} of {len(parts)} searches failed ({'; '.join(failures)})"
            if failures
            else None
        ),
    )


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


def plant_assets(rows: list[dict[str, Any]]) -> list[Asset]:
    """EIA-860 plants, those with fossil units retiring or retired first, then
    the largest."""
    assets = []
    for row in rows:
        retiring = float(row.get("planned_retirement_mw") or 0.0)
        retired = float(row.get("retired_fossil_mw") or 0.0)
        assets.append(
            Asset(
                id=f"eia860:{row['plant_id']}",
                type="power_plant",
                name=_display_name(row.get("name"), "Unnamed power plant"),
                lat=row["lat"],
                lon=row["lon"],
                attributes={
                    "technology": row.get("primary_technology"),
                    "nameplate_mw": _rounded(row.get("operable_mw"), 1),
                    "fossil_mw": _rounded(row.get("fossil_mw"), 1),
                    "planned_retirement_mw": _rounded(retiring, 1) if retiring else None,
                    "planned_retirement_year": row.get("planned_retirement_year"),
                    "retired_fossil_mw": _rounded(retired, 1) if retired else None,
                    "last_retirement_year": row.get("last_retirement_year"),
                    # Fossil capacity freed or to be freed: what repowering would reuse.
                    "reuse_mw": _rounded(retiring + retired, 1) if retiring + retired else None,
                    "reuse_candidate": bool(row.get("reuse_candidate")),
                    "grid_voltage_kv": row.get("grid_voltage_kv"),
                    "utility_name": row.get("utility_name"),
                    "sector": row.get("sector"),
                    "city": _display_name(row.get("city"), "") or None,
                },
                source=power_plants.SOURCE_LABEL,
            )
        )
    assets.sort(
        key=lambda a: (
            not a.attributes["reuse_candidate"],
            -float(a.attributes.get("reuse_mw") or 0.0),  # type: ignore[arg-type]
            -float(a.attributes.get("nameplate_mw") or 0.0),  # type: ignore[arg-type]
        )
    )
    return assets


def _inside(county: County, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mask = county.contains_points([r["lon"] for r in rows], [r["lat"] for r in rows])
    return [row for row, keep in zip(rows, mask, strict=True) if keep]


def building_assets(county: County, rows: list[dict[str, Any]]) -> list[Asset]:
    return [
        Asset(
            id=f"geoapify:{row['place_id']}",
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


# --- opportunities ----------------------------------------------------------------


@dataclass(frozen=True)
class _Proposal:
    """An opportunity before code adds its estimates and funding leads."""

    id: str
    title: str
    # The project type, when it is one data/solutions.json knows.
    solution: matching.Solution | None
    needs: list[Need]
    # The sites it uses. For a typed proposal the first is the host the
    # estimates are sized on.
    assets: list[Asset]
    score: int
    generated_by: Literal["gemini", "rules"]
    summary: str | None = None
    rationale: str | None = None
    first_steps: tuple[str, ...] = ()
    factor_ids: tuple[str, ...] = ()

    @property
    def host(self) -> Asset | None:
        return self.assets[0] if self.solution and self.assets else None


def proposals_from_drafts(
    drafts: list[opportunities.Draft],
    flagged: list[Need],
    assets: list[Asset],
    solutions: tuple[matching.Solution, ...],
) -> list[_Proposal]:
    """Gemini's drafts, scored and ranked by code. A draft keeps its project
    type only if it names a site that type can be built at; without one there
    is nothing to size, so it is treated as an idea of Gemini's own."""
    assets_by_id = {a.id: a for a in assets}
    solutions_by_id = {s.id: s for s in solutions}
    proposals: list[_Proposal] = []
    for number, draft in enumerate(drafts, start=1):
        addressed = [n for n in flagged if n.id in draft.need_ids]
        named = [assets_by_id[i] for i in draft.asset_ids if i in assets_by_id]
        solution = solutions_by_id.get(draft.solution_id or "")
        factor = 1.0
        if solution is not None and solution.asset_kind != "county":
            hosts = [
                a
                for a in named
                if a.type in solution.hosts
                and (solution.host_requires is None or a.attributes.get(solution.host_requires))
            ]
            if hosts:
                named = [hosts[0], *(a for a in named if a.id != hosts[0].id)]
                if not estimates.has_estimate_inputs(solution.asset_kind, hosts[0].attributes):
                    factor = matching.MISSING_DATA_FACTOR
            else:
                solution = None
        proposals.append(
            _Proposal(
                id=f"ai:{number}",
                title=draft.title,
                solution=solution,
                needs=addressed,
                assets=named,
                score=round(matching.need_score(addressed) * factor),
                generated_by="gemini",
                summary=draft.summary,
                rationale=draft.rationale,
                first_steps=tuple(draft.first_steps),
                factor_ids=tuple(draft.factor_ids),
            )
        )
    proposals.sort(key=lambda p: -p.score)  # stable: Gemini's order breaks ties
    return proposals


def proposals_from_matches(matches: list[matching.Match]) -> list[_Proposal]:
    return [
        _Proposal(
            id=match.id,
            title=match.title,
            solution=match.solution,
            needs=match.needs,
            assets=[match.asset] if match.asset else [],
            score=match.score,
            generated_by="rules",
        )
        for match in matches
    ]


def size_estimates(
    solution_id: str, asset: Asset, solar_yield: float | None, site_specific: bool
) -> list[Estimate]:
    """The labelled estimates for one project type at one site."""
    if solution_id == "community_solar":
        acres = asset.attributes.get("acres")
        return estimates.solar_on_land(
            float(acres) if isinstance(acres, (int, float)) else None, solar_yield, site_specific
        )
    if solution_id == "resilience_hub":
        return estimates.resilience_hub(solar_yield, site_specific)
    if solution_id == "landfill_gas_energy":
        return estimates.landfill_gas(asset.attributes)
    if solution_id == "small_hydro":
        return estimates.hydro_at_dam(asset.attributes)
    if solution_id == "wind_on_reclaimed_land":
        return estimates.wind_on_land(asset.attributes)
    return []


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
            settings.buildings_timeout_s,
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
        _run(
            "earthengine",
            lambda: earthengine.fetch_county_metrics(settings, county),
            settings.ee_timeout_s,
        ),
        _run("svi", _local(lambda: svi.svi_for_county(data_dir, county.fips)), timeout),
        _run(
            "climate",
            lambda: openmeteo.fetch_climate(
                client, settings, county.centroid_lat, county.centroid_lon
            ),
            timeout,
        ),
        _run("peers", lambda: census.fetch_all_counties(client, settings), timeout),
        # Sources added 2026-11-14.
        _run(
            "energy_burden",
            _local(lambda: energy_burden.burden_for_county(data_dir, county.fips)),
            timeout,
        ),
        _run(
            "energy_communities",
            _local(lambda: energy_communities.status_for_county(data_dir, county.fips)),
            timeout,
        ),
        _run(
            "power_plants",
            _local(lambda: power_plants.plants_for_county(data_dir, county.fips)),
            timeout,
        ),
        _run(
            "utilities",
            _local(lambda: utilities.utilities_for_county(data_dir, county.fips)),
            timeout,
        ),
        _run("rucc", _local(lambda: rucc.rucc_for_county(data_dir, county.fips)), timeout),
        _run("places", _local(lambda: places.places_for_county(data_dir, county.fips)), timeout),
        _run(
            "air_quality",
            _local(lambda: air_quality.design_values_for_county(data_dir, county.fips)),
            timeout,
        ),
        _run(
            "wildfire", _local(lambda: wildfire.wildfire_for_county(data_dir, county.fips)), timeout
        ),
        _run(
            "disasters",
            lambda: openfema.fetch_declarations(
                client, settings, county.state_fips, county.county_code
            ),
            timeout,
        ),
        _run(
            "drinking_water",
            lambda: drinking_water.fetch_water_systems(client, settings, county.fips),
            timeout,
        ),
        _run(
            "waters",
            lambda: attains.fetch_county_waters(client, settings, county.fips, county.geometry),
            timeout,
        ),
        _run(
            "wind_turbines", lambda: uswtdb.fetch_turbines(client, settings, county.fips), timeout
        ),
        _run(
            "transit",
            lambda: transit.fetch_stops(client, settings, county.fips, county.geometry),
            timeout,
        ),
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
        (ee_metrics, _),
        (svi_row, _),
        (climate, _),
        (all_county_values, peers_status),
        (burden, _),
        (energy_community, _),
        (plant_rows, _),
        (utility_rows, _),
        (rucc_row, _),
        (places_row, _),
        (air_values, _),
        (wildfire_row, _),
        (disasters, _),
        (water_systems, _),
        (waters, _),
        (turbines, _),
        (transit_row, _),
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
    plants = plant_assets(plant_rows or [])
    all_assets = sites + landfills + buildings + stations + dam_sites + plants

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
        needs.urban_heat_need(ee_metrics, settings),
        needs.low_tree_canopy_need(ee_metrics, settings),
        needs.energy_burden_need(burden, settings),
        needs.utility_shutoff_need(places_row, settings),
        needs.drinking_water_need(water_systems, settings),
        needs.impaired_waters_need(waters, settings),
        needs.air_quality_need(air_values, settings),
    ):
        if extra:
            flagged.append(extra)
    flagged = needs.prioritize(flagged, settings)

    # --- peer counties: places of similar size, income and density ---------------
    peer_counties: list[PeerCounty] = []
    people_per_sq_mile: float | None = None
    if all_county_values is not None:
        try:
            directory = peers.load_directory(data_dir / "counties.geojson")
            peer_counties = peers.find_peers(
                county.fips, all_county_values, directory, acs_national, flagged, settings
            )
            own_density = peers.density(acs.population if acs else None, directory.get(county.fips))
            people_per_sq_mile = round(own_density, 1) if own_density else None
        except SourceError as exc:
            peers_status.ok, peers_status.message = False, str(exc)

    # --- assets and factors: what the county has to work with --------------------
    # The profile (trades and industries, social vulnerability, weather history,
    # land cover and terrain) joins the other factors, so Gemini builds the
    # opportunities from all of it.
    county_profile = (
        (profile.acs_profile(acs, acs_national, settings) if acs else [])
        + profile.svi_profile(svi_row)
        + profile.climate_profile(climate)
        + profile.land_profile(ee_metrics)
        + profile.places_profile(places_row)
    )
    county_factors = factors.build_factors(
        acs=acs,
        acs_national=acs_national,
        energy=energy,
        nri_row=nri_row,
        solar_yield=centroid_yield,
        farm_row=farm_row,
        ee_metrics=ee_metrics,
        assets=all_assets,
        settings=settings,
        profile=county_profile,
        extra=factors.CountyExtras(
            burden=burden,
            utilities=utility_rows,
            energy_community=energy_community,
            energy_community_checked=ok["energy_communities"],
            turbines=turbines,
            rucc=rucc_row,
            disasters=disasters,
            wildfire=wildfire_row,
            air=air_values,
            water_systems=water_systems,
            waters=waters,
            transit=transit_row,
        ),
    )
    counts = Counter(a.type for a in all_assets)
    searched: list[tuple[str, tuple[str, ...]]] = [
        ("repowering", repowering.SITE_TYPES),
        ("lmop", ("lmop_landfill",)),
        ("geoapify", BUILDING_TYPES),
        ("ev_stations", ("ev_station",)),
        ("dams", ("non_powered_dam",)),
        ("power_plants", ("power_plant",)),
    ]
    missing = [t for source, types in searched if ok[source] for t in types if counts[t] == 0]

    # --- opportunities: Gemini writes them from the needs, factors and sites ------
    solutions = matching.load_solutions(data_dir / "solutions.json")
    digest = opportunities.build_digest(
        county.name,
        county.state,
        flagged,
        county_factors,
        all_assets,
        missing,
        solutions,
        settings.digest_sites_per_type,
    )
    failed = [SOURCE_NAMES.get(s.source, s.source) for s in statuses if not s.ok]
    plan, gemini_status = await opportunities.write_opportunities(digest, settings)
    statuses.append(gemini_status)
    if plan is not None:
        proposals = proposals_from_drafts(plan.drafts, flagged, all_assets, solutions)
    else:  # no key, an error, a timeout or nothing usable: the rule matches
        proposals = proposals_from_matches(
            matching.match_opportunities(
                flagged,
                all_assets,
                solutions,
                centroid,
                settings.assets_per_solution,
                settings.max_opportunities,
            )
        )

    # --- site-specific solar yield for the top sites ----------------------------
    solar_assets: dict[str, Asset] = {}
    for proposal in proposals:
        host = proposal.host
        if (
            host is not None
            and proposal.solution is not None
            and proposal.solution.id in SOLAR_SOLUTIONS
            and len(solar_assets) < settings.max_pvwatts_sites
        ):
            solar_assets.setdefault(host.id, host)
    # Grants.gov searches for the typed opportunities run alongside, so they add
    # no wait. Leads are found by hand-written queries, never by Gemini.
    grant_queries = grantsgov.load_queries(data_dir / "grants_queries.json")
    keywords = grantsgov.keywords_for(
        grant_queries,
        [(p.solution.id, p.host.type if p.host else None) for p in proposals if p.solution],
    )
    gathered = await asyncio.gather(
        *(
            _run(
                "pvwatts_site",
                lambda a=a: pvwatts.fetch_yield(client, settings, a.lat, a.lon),
                timeout,
            )
            for a in solar_assets.values()
        ),
        *(
            _run("grants_gov", lambda k=k: grantsgov.search(client, settings, k), timeout)
            for k in keywords
        ),
    )
    site_results = gathered[: len(solar_assets)]
    grant_results = gathered[len(solar_assets) :]
    grant_searches = {
        keyword: value
        for keyword, (value, _) in zip(keywords, grant_results, strict=True)
        if value is not None
    }
    if keywords:
        statuses.append(combined_status("grants_gov", keywords, [s for _, s in grant_results]))
    site_yields = {
        asset_id: value
        for asset_id, (value, _) in zip(solar_assets, site_results, strict=True)
        if value is not None
    }

    # --- estimates and funding -------------------------------------------------
    # Only a typed opportunity gets these: an idea of Gemini's own has no
    # project type to size or to look up in the funding table.
    programs = funding.load_programs(data_dir / "funding_programs.json")
    built: list[Opportunity] = []
    for proposal in proposals:
        solution, host = proposal.solution, proposal.host
        figures: list[Estimate] = []
        if solution is not None and host is not None:
            site_yield = site_yields.get(host.id)
            figures = size_estimates(
                solution.id,
                host,
                site_yield if site_yield is not None else centroid_yield,
                site_yield is not None,
            )
        # The curated table's matches and the live Grants.gov listings, in one
        # list ordered like the table: actionable first, then by deadline.
        leads = (
            funding.match_funding(
                programs,
                solution.id,
                host.type if host else None,
                energy_community=energy_community,
                energy_community_checked=ok["energy_communities"],
            )
            + grantsgov.leads_for(
                grant_queries,
                grant_searches,
                solution.id,
                host.type if host else None,
                settings.max_grant_leads,
            )
            if solution
            else []
        )
        leads.sort(key=lambda f: (funding.STATUS_ORDER[f.status], f.deadline or "9999"))
        built.append(
            Opportunity(
                id=proposal.id,
                solution_id=solution.id if solution else None,
                title=proposal.title,
                summary=proposal.summary,
                rationale=proposal.rationale,
                first_steps=list(proposal.first_steps),
                need_ids=[n.id for n in proposal.needs],
                asset_ids=[a.id for a in proposal.assets],
                factor_ids=list(proposal.factor_ids),
                score=proposal.score,
                estimates=figures,
                funding=leads,
                generated_by=proposal.generated_by,
            )
        )

    population = acs.population if acs else None
    context = CountyContext(
        median_household_income=acs.median_household_income if acs else None,
        poverty_rate=acs.poverty_rate if acs else None,
        unemployment_rate=acs.unemployment_rate if acs else None,
        electric_heat_share=acs.electric_heat_share if acs else None,
        state_electricity_price_cents_kwh=(
            energy["state"].get("residential_price_cents_kwh") if energy else None
        ),
        state_grid_co2_lb_per_mwh=energy["state"].get("grid_co2_lb_per_mwh") if energy else None,
    )
    resources = Resources(
        solar_kwh_per_kw_year=round(centroid_yield) if centroid_yield else None,
        land_in_farms_acres=farm_row.get("land_in_farms_acres") if farm_row else None,
        cropland_acres=farm_row.get("cropland_acres") if farm_row else None,
    )

    # --- the brief: Gemini's summary from the same call, or the template ----------
    payload = narrative.build_payload(county.name, county.state, flagged, built)
    brief = narrative.compose(payload, failed, plan.summary if plan else None)

    # --- what the map gets: capped, but never without a site the report names -----
    used = {asset_id for o in built for asset_id in o.asset_ids}
    shown_assets = _cap_for_map(
        [
            (sites, settings.max_sites_per_county),
            (landfills, len(landfills)),
            (buildings, settings.max_buildings_per_county),
            (stations, settings.max_ev_markers_per_county),
            (dam_sites, len(dam_sites)),
            (plants, settings.max_power_plants_per_county),
        ],
        used,
    )

    return CountyReport(
        fips=county.fips,
        name=county.name,
        state=county.state,
        population=population,
        context=context,
        needs=flagged,
        assets=shown_assets,
        asset_counts=dict(counts),
        missing_asset_types=missing,  # type: ignore[arg-type]
        resources=resources,
        factors=county_factors,
        highlights=highlights.find_highlights(
            factors=county_factors,
            profile=county_profile,
            acs=acs,
            acs_national=acs_national,
            energy=energy,
            ee_metrics=ee_metrics,
            farm_row=farm_row,
            assets=all_assets,
            settings=settings,
        ),
        opportunities=built,
        peers=peer_counties,
        people_per_sq_mile=people_per_sq_mile,
        narrative=brief,
        source_status=statuses,
        scout_available=county.fips in settings.scout_counties,
    )
