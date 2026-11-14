"""Assets, factors, knowledge: everything about a county that a solution
could draw on.

A need says what is wrong. A factor says what the county has to work with:
its climate, land, buildings, workforce, economy, community, energy system,
infrastructure and farms. Factors are county-level facts; individual places
are the mapped assets. Gemini is given both, with the needs, and writes the
opportunities from them (opportunities.py).

Some factors are built here. The rest come from profile.py (trades and
industries, schooling, social vulnerability, weather history, land cover and
terrain) and are folded into the same list.

Pure functions. A factor is listed only when its source loaded; nothing here
is estimated, and every figure comes straight from a source or is a count or
sum of what the source lists.
"""

from __future__ import annotations

import math
import string
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.models import Asset, Factor, FactorCategory, ProfileCategory, ProfileFact
from app.profile import format_profile_value
from app.sources import (
    afdc,
    air_quality,
    attains,
    dams,
    drinking_water,
    earthengine,
    energy_burden,
    energy_communities,
    farms,
    geoapify,
    lmop,
    nri,
    openfema,
    power_plants,
    repowering,
    rucc,
    state_energy,
    transit,
    uswtdb,
    utilities,
    wildfire,
)
from app.sources.census import AcsMetrics


@dataclass(frozen=True)
class CountyExtras:
    """What the sources added on 2026-11-14 return for one county. Each is
    None (or empty) when the source failed or has nothing for the county."""

    burden: dict[str, Any] | None = None
    utilities: list[dict[str, Any]] | None = None
    energy_community: dict[str, Any] | None = None
    # False when the energy community file did not load: then nothing is said.
    energy_community_checked: bool = False
    turbines: dict[str, Any] | None = None
    rucc: dict[str, Any] | None = None
    disasters: dict[str, Any] | None = None
    wildfire: dict[str, Any] | None = None
    air: dict[str, Any] | None = None
    water_systems: list[dict[str, Any]] | None = None
    waters: dict[str, Any] | None = None
    transit: dict[str, Any] | None = None

PVWATTS_LABEL = "NLR PVWatts v8"

# The order the panel and the prompt list them in.
CATEGORY_ORDER: tuple[FactorCategory, ...] = (
    "climate",
    "energy",
    "land",
    "buildings",
    "workforce",
    "economy",
    "community",
    "infrastructure",
    "agriculture",
)

# profile.py's categories, as factor categories.
PROFILE_CATEGORIES: dict[ProfileCategory, FactorCategory] = {
    "workforce": "workforce",
    "economy": "economy",
    "community": "community",
    "climate": "climate",
    "geography": "land",
}

LAND_LABELS = {
    "brownfield": "Brownfields",
    "contaminated_site": "Cleanup sites (Superfund, RCRA or state)",
    "landfill": "Landfills (EPA RE-Powering list)",
    "mine_land": "Abandoned mine lands",
}


def _number(value: Any) -> float | None:
    """A finite number, or None. Parquet rows carry NaN for missing values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _share(value: float | None) -> str | None:
    return f"{value * 100:.1f}%" if value is not None else None


def _count(count: int, singular: str, plural: str) -> str:
    return f"{count:,} {singular if count == 1 else plural}"


def _us(text: str | None) -> str | None:
    return f"US: {text}" if text else None


class _Builder:
    def __init__(self) -> None:
        self.factors: list[Factor] = []

    def add(
        self,
        factor_id: str,
        category: FactorCategory,
        label: str,
        value: str | None,
        source: str,
        comparison: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Add the factor, unless the source had no value for it."""
        if value:
            self.factors.append(
                Factor(
                    id=factor_id,
                    category=category,
                    label=label,
                    value=value,
                    comparison=comparison,
                    detail=detail,
                    source=source,
                )
            )


def _acs_factors(b: _Builder, acs: AcsMetrics, national: AcsMetrics | None, source: str) -> None:
    def share(
        factor_id: str,
        category: FactorCategory,
        label: str,
        field: str,
        detail: str | None = None,
    ) -> None:
        value = _share(getattr(acs, field))
        us = _share(getattr(national, field)) if national else None
        b.add(factor_id, category, label, value, source, _us(us), detail)

    us_income = national.median_household_income if national else None
    income = acs.median_household_income

    # Workforce: who is free to be hired. The trades, industries and schooling
    # of the people in work come from profile.py.
    share(
        "unemployment",
        "workforce",
        "Unemployment rate",
        "unemployment_rate",
        "Share of the civilian labor force that is out of work and looking for it.",
    )

    # Economy.
    b.add(
        "population",
        "economy",
        "Population",
        f"{acs.population:,} residents" if acs.population is not None else None,
        source,
    )
    b.add(
        "median_household_income",
        "economy",
        "Median household income",
        f"${income:,.0f}" if income is not None else None,
        source,
        _us(f"${us_income:,.0f}" if us_income is not None else None),
    )
    share("poverty_rate", "economy", "Residents below the poverty line", "poverty_rate")

    # Buildings: the housing stock.
    b.add(
        "housing_units",
        "buildings",
        "Homes",
        f"{acs.housing_units:,} housing units" if acs.housing_units is not None else None,
        source,
    )
    share("housing_pre1980", "buildings", "Homes built before 1980", "pre1980_housing_share")
    share("vacant_housing", "buildings", "Homes that are vacant", "vacant_housing_share")
    share("mobile_homes", "buildings", "Mobile and manufactured homes", "mobile_home_share")
    share("renter_households", "buildings", "Households that rent", "renter_share")

    # Energy: how homes are heated.
    share("electric_heat", "energy", "Homes heated with electricity", "electric_heat_share")
    share(
        "delivered_fuel_heat",
        "energy",
        "Homes heated with fuel oil or propane",
        "delivered_fuel_heat_share",
    )


def _energy_factors(b: _Builder, energy: dict[str, Any]) -> None:
    state, us, abbr = energy["state"], energy["national"], energy["state_abbr"]
    bill_source = state_energy.bill_source_label(energy["electricity_year"])
    note = f"State average for {abbr}; not published by county."

    def figure(row: dict[str, Any], key: str, template: str) -> str | None:
        value = _number(row.get(key))
        return template.format(value) if value is not None else None

    b.add(
        "electricity_price",
        "energy",
        "Household electricity price",
        figure(state, "residential_price_cents_kwh", "{:.1f} cents per kWh"),
        bill_source,
        _us(figure(us, "residential_price_cents_kwh", "{:.1f} cents per kWh")),
        note,
    )
    b.add(
        "electricity_bill",
        "energy",
        "Average household electricity bill",
        figure(state, "residential_monthly_bill_usd", "${:,.0f} a month"),
        bill_source,
        _us(figure(us, "residential_monthly_bill_usd", "${:,.0f} a month")),
        note,
    )
    b.add(
        "grid_carbon",
        "energy",
        "Carbon intensity of electricity generated in the state",
        figure(state, "grid_co2_lb_per_mwh", "{:,.0f} pounds of CO2 per MWh"),
        state_energy.grid_source_label(energy["grid_year"]),
        _us(figure(us, "grid_co2_lb_per_mwh", "{:,.0f} pounds of CO2 per MWh")),
        note,
    )


def _climate_factors(
    b: _Builder,
    solar_yield: float | None,
    nri_row: dict[str, Any] | None,
    ee_metrics: dict[str, Any] | None,
    settings: Settings,
) -> None:
    b.add(
        "solar_resource",
        "climate",
        "Solar resource",
        f"{solar_yield:,.0f} kWh a year per kW of panels" if solar_yield else None,
        PVWATTS_LABEL,
        detail="Typical-year output of a fixed south-facing array at the county center.",
    )
    if nri_row:
        ratings = [
            f"{hazard}: {nri_row[column]}"
            for column, hazard in nri.HAZARDS.items()
            if isinstance(nri_row.get(column), str) and nri_row[column]
        ]
        b.add(
            "natural_hazards",
            "climate",
            "Natural hazard risk ratings",
            "; ".join(ratings),
            nri.source_label(nri_row.get("nri_version")),
            detail="FEMA rates each hazard from Very Low to Very High against other US counties.",
        )
    if ee_metrics:
        built, natural = ee_metrics.get("lst_built_up_c"), ee_metrics.get("lst_natural_c")
        if built is not None and natural is not None:
            b.add(
                "summer_surface_heat",
                "climate",
                "Summer daytime surface temperature",
                f"{built} C on buildings and pavement; {natural} C on natural land",
                earthengine.SOURCE_LABEL,
                detail="Landsat, June to August, 2021-2025.",
            )
        b.add(
            "tree_canopy",
            "climate",
            "Tree canopy over built-up land",
            _share(ee_metrics.get("canopy_built_up")),
            earthengine.SOURCE_LABEL,
            _us(_share(settings.tree_canopy_built_up_national)),
        )


def _site_factors(b: _Builder, assets: list[Asset]) -> None:
    by_type: dict[str, list[Asset]] = {}
    for asset in assets:
        by_type.setdefault(asset.type, []).append(asset)

    for asset_type, label in LAND_LABELS.items():
        found = by_type.get(asset_type, [])
        if not found:
            continue
        acres = [a for a in (_number(s.attributes.get("acres")) for s in found) if a]
        value = _count(len(found), "site", "sites")
        if acres:
            value += f"; {sum(acres):,.0f} acres across the {len(acres):,} with a recorded size"
        b.add(
            f"sites_{asset_type}",
            "land",
            label,
            value,
            repowering.SOURCE_LABEL,
            detail="Screened by EPA for renewable energy potential.",
        )

    buildings = [
        _count(len(by_type[t]), singular, plural)
        for t, singular, plural in (
            ("school", "school", "schools"),
            ("library", "library", "libraries"),
            ("college", "college or university", "colleges and universities"),
        )
        if by_type.get(t)
    ]
    b.add(
        "public_buildings",
        "buildings",
        "Public buildings",
        ", ".join(buildings),
        geoapify.SOURCE_LABEL,
    )

    stations = by_type.get("ev_station", [])
    if stations:
        b.add(
            "ev_stations",
            "infrastructure",
            "Public EV charging",
            _count(len(stations), "station", "stations"),
            afdc.SOURCE_LABEL,
        )

    dam_sites = by_type.get("non_powered_dam", [])
    if dam_sites:
        potential = [m for m in (_number(d.attributes.get("capacity_mw")) for d in dam_sites) if m]
        b.add(
            "non_powered_dams",
            "infrastructure",
            "Dams without hydropower",
            _count(len(dam_sites), "dam", "dams"),
            dams.SOURCE_LABEL,
            detail=(
                f"ORNL models {sum(potential):,.1f} MW of hydropower potential across them."
                if potential
                else None
            ),
        )

    landfills = by_type.get("lmop_landfill", [])
    if landfills:
        operating = sum(1 for a in landfills if a.attributes.get("has_operational_project"))
        candidates = sum(1 for a in landfills if a.attributes.get("is_candidate"))
        b.add(
            "landfill_gas_sites",
            "infrastructure",
            "Landfills in EPA's landfill gas database",
            _count(len(landfills), "landfill", "landfills"),
            lmop.SOURCE_LABEL,
            detail=(
                f"{operating} with an operating gas energy project; "
                f"{candidates} that EPA lists as a candidate for one. "
                "The database does not include every landfill."
            ),
        )


def _farm_factors(b: _Builder, farm_row: dict[str, Any]) -> None:
    land = _number(farm_row.get("land_in_farms_acres"))
    cropland = _number(farm_row.get("cropland_acres"))
    parts = []
    if land:
        parts.append(f"{land:,.0f} acres in farms")
    if cropland:
        parts.append(f"{cropland:,.0f} acres of cropland")
    b.add("farmland", "agriculture", "Farmland", "; ".join(parts), farms.CENSUS_LABEL)

    dairies = int(_number(farm_row.get("dairy_farms_500_plus")) or 0)
    hog_farms = int(_number(farm_row.get("hog_farms_1000_plus")) or 0)
    digesters = int(_number(farm_row.get("digesters_operational")) or 0)
    herds = []
    if dairies:
        herds.append(_count(dairies, "dairy farm", "dairy farms") + " with 500 or more cows")
    if hog_farms:
        herds.append(_count(hog_farms, "hog farm", "hog farms") + " with 1,000 or more hogs")
    b.add(
        "large_livestock_farms",
        "agriculture",
        "Large livestock farms",
        "; ".join(herds),
        farms.SOURCE_LABEL,
        detail=(
            f"EPA lists {_count(digesters, 'operating digester', 'operating digesters')} for "
            "manure in the county. The census gives counts of farms, not their locations."
        ),
    )


def _energy_burden_factor(b: _Builder, burden: dict[str, Any]) -> None:
    county, us = burden["county"], burden.get("national") or {}

    def text(row: dict[str, Any]) -> str | None:
        low, overall = _number(row.get("burden_low_income")), _number(row.get("burden_all"))
        parts = []
        if low is not None:
            parts.append(f"{_share(low)} for households below 200% of the poverty line")
        if overall is not None:
            parts.append(f"{_share(overall)} for all households")
        return "; ".join(parts) or None

    b.add(
        "energy_burden",
        "energy",
        "Home energy bills as a share of income",
        text(county),
        energy_burden.SOURCE_LABEL,
        _us(text(us)),
        "Electricity, gas and other home fuels, summed over households (DOE LEAD method).",
    )


OWNERSHIP_NAMES = {
    "Cooperative": "co-op",
    "Investor Owned": "investor-owned",
    "Municipal": "municipal",
    "Political Subdivision": "public power district",
    "Federal": "federal",
    "State": "state",
}


def _utility_factors(b: _Builder, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    shown = []
    for row in rows[:4]:
        ownership = str(row.get("ownership") or "")
        kind = OWNERSHIP_NAMES.get(ownership, ownership.lower())
        price = _number(row.get("residential_price_cents_kwh"))
        bits = [kind] if kind else []
        if price is not None:
            bits.append(f"{price:.1f} cents per kWh for homes")
        shown.append(f"{row['utility_name']} ({', '.join(bits)})" if bits else row["utility_name"])
    if len(rows) > 4:
        shown.append(f"and {len(rows) - 4} more")
    b.add(
        "electric_utilities",
        "energy",
        "Electric utilities serving the county",
        "; ".join(shown),
        utilities.SOURCE_LABEL,
        detail=(
            "Largest first, by the utility's residential customers in the state; EIA does not "
            "publish customers by county. Prices are each utility's statewide residential "
            "average for full (bundled) service."
        ),
    )


def _energy_community_factor(b: _Builder, status: dict[str, Any] | None, checked: bool) -> None:
    if not checked:
        return
    if energy_communities.is_statistical_area(status):
        value = "Yes, the whole county (statistical-area list, 2025 unemployment)"
    elif energy_communities.coal_closure_tracts(status):
        total = int((status or {}).get("total_tracts") or 0)
        tracts = energy_communities.coal_closure_tracts(status)
        value = f"Part of the county: {tracts} of {total} census tracts (coal closure)"
    else:
        value = "No: no statistical area or coal-closure tract; brownfield sites may still qualify"
    b.add(
        "energy_community",
        "energy",
        "Energy community for the clean electricity tax credit bonus",
        value,
        energy_communities.SOURCE_LABEL,
        detail=(
            "Projects in an energy community get a bonus on the federal clean electricity "
            "credits. Statistical areas are set each year from fossil fuel jobs and "
            "unemployment; coal-closure tracts have a closed coal mine or retired coal plant, "
            "or border one."
        ),
    )


def _turbine_factor(b: _Builder, turbines: dict[str, Any]) -> None:
    count = int(turbines.get("turbines") or 0)
    if count == 0:
        b.add(
            "wind_turbines",
            "energy",
            "Wind turbines",
            "None in the US Wind Turbine Database",
            uswtdb.SOURCE_LABEL,
        )
        return
    value = _count(count, "turbine", "turbines")
    projects = int(turbines.get("projects") or 0)
    if projects:
        value += f" in {_count(projects, 'project', 'projects')}"
    capacity = _number(turbines.get("capacity_mw"))
    if capacity:
        value += f", {capacity:,.1f} MW"
    latest = turbines.get("latest_year")
    b.add(
        "wind_turbines",
        "energy",
        "Wind turbines",
        value,
        uswtdb.SOURCE_LABEL,
        detail=f"Newest project online in {latest}." if latest else None,
    )


def _plant_factor(b: _Builder, assets: list[Asset]) -> None:
    plants = [a for a in assets if a.type == "power_plant"]
    if not plants:
        return
    by_technology: dict[str, float] = {}
    for plant in plants:
        mw = _number(plant.attributes.get("nameplate_mw")) or 0.0
        tech = str(plant.attributes.get("technology") or "Other")
        if mw:
            by_technology[tech] = by_technology.get(tech, 0.0) + mw
    total = sum(by_technology.values())
    top = sorted(by_technology.items(), key=lambda kv: -kv[1])[:3]
    value = _count(len(plants), "plant", "plants")
    if total:
        value += f", {total:,.0f} MW operating"
        value += ": " + ", ".join(f"{tech} {mw:,.0f} MW" for tech, mw in top)
    reuse = [p for p in plants if p.attributes.get("reuse_candidate")]
    detail = None
    if reuse:
        retiring = sum(_number(p.attributes.get("planned_retirement_mw")) or 0 for p in reuse)
        retired = sum(_number(p.attributes.get("retired_fossil_mw")) or 0 for p in reuse)
        bits = []
        if retiring:
            bits.append(f"{retiring:,.0f} MW of fossil units with a planned retirement date")
        if retired:
            bits.append(f"{retired:,.0f} MW of fossil units retired since 2010")
        detail = (
            f"{_count(len(reuse), 'plant has', 'plants have')} {' and '.join(bits)}: their grid "
            "connections could serve new clean power or storage."
        )
    b.add(
        "power_plants",
        "infrastructure",
        "Power plants of 1 MW or more",
        value,
        power_plants.SOURCE_LABEL,
        detail=detail,
    )


def _rucc_factor(b: _Builder, row: dict[str, Any]) -> None:
    code = row.get("rucc_code")
    description = row.get("rucc_description")
    if code is None or not description:
        return
    b.add(
        "rural_urban",
        "community",
        "Rural-urban classification",
        f"Code {int(code)} of 9: {description}",
        rucc.SOURCE_LABEL,
        detail="1-3 are metro counties and 4-9 nonmetro; higher codes are more rural.",
    )


def _disaster_factor(b: _Builder, disasters: dict[str, Any]) -> None:
    count = int(disasters.get("count") or 0)
    since = disasters.get("since_year")
    label = f"Major federal disaster declarations since {since}"
    if count == 0:
        b.add("disaster_declarations", "climate", label, "None", openfema.SOURCE_LABEL)
        return
    kinds = ", ".join(
        f"{n} {kind.lower()}" for kind, n in list((disasters.get("by_type") or {}).items())[:4]
    )
    latest = disasters.get("latest_title")
    date = disasters.get("latest_date")
    b.add(
        "disaster_declarations",
        "climate",
        label,
        f"{_count(count, 'declaration', 'declarations')}: {kinds}",
        openfema.SOURCE_LABEL,
        detail=(
            f"Most recent: {string.capwords(latest.lower())} ({date})." if latest and date else None
        ),
    )


def _wildfire_factor(b: _Builder, row: dict[str, Any]) -> None:
    rank = _number(row.get("risk_to_homes_national_rank"))
    exposed = _number(row.get("housing_directly_exposed"))
    parts = []
    if rank is not None:
        parts.append(f"{format_profile_value(rank, 'percentile')} among US counties")
    if exposed is not None:
        parts.append(f"{_share(exposed)} of homes directly exposed")
    b.add(
        "wildfire_risk_to_homes",
        "climate",
        "Wildfire risk to homes",
        "; ".join(parts),
        wildfire.SOURCE_LABEL,
        detail=(
            "Directly exposed homes sit in or next to burnable vegetation. Risk combines how "
            "likely and how intense fire is with how much a home would be damaged."
        ),
    )


def _air_factor(b: _Builder, values: dict[str, Any], settings: Settings) -> None:
    parts = []
    pm = _number(values.get("pm25_annual_ugm3"))
    ozone = _number(values.get("ozone_ppm"))
    if pm is not None:
        parts.append(f"PM2.5 {pm:.1f} ug/m3 (standard {settings.pm25_annual_standard_ugm3:.1f})")
    if ozone is not None:
        parts.append(f"ozone {ozone:.3f} ppm (standard {settings.ozone_standard_ppm:.3f})")
    b.add(
        "air_quality",
        "climate",
        "Air pollution at the county's monitors",
        "; ".join(parts),
        air_quality.SOURCE_LABEL,
        detail="Highest 2023-2025 design value among the county's monitors.",
    )


def _water_system_factor(b: _Builder, systems: list[dict[str, Any]]) -> None:
    if not systems:
        return
    served = sum(int(s.get("population") or 0) for s in systems)
    flagged = sum(1 for s in systems if s.get("health_violation"))
    lead = sum(1 for s in systems if s.get("lead_exceedances"))
    value = f"{_count(len(systems), 'system', 'systems')} serving {served:,} people"
    if flagged:
        value += f"; {flagged} with a health-based violation"
    b.add(
        "drinking_water_systems",
        "infrastructure",
        "Community drinking water systems",
        value,
        drinking_water.SOURCE_LABEL,
        detail=(
            f"{_count(lead, 'system', 'systems')} exceeded the lead action level in the past "
            "five years. "
            if lead
            else ""
        )
        + "People served counts each system's whole service area, which can cross county lines.",
    )


def _waters_factor(b: _Builder, waters: dict[str, Any]) -> None:
    county, us = waters.get("county") or {}, waters.get("national") or {}
    assessed, impaired = int(county.get("assessed") or 0), int(county.get("impaired") or 0)
    if not assessed:
        return
    us_assessed = int(us.get("assessed") or 0)
    us_share = int(us.get("impaired") or 0) / us_assessed if us_assessed else None
    causes = list((county.get("causes") or {}).keys())[:3]
    b.add(
        "assessed_waters",
        "land",
        "Streams and lakes that fail water quality standards",
        f"{impaired:,} of {assessed:,} assessed ({_share(impaired / assessed)})",
        attains.SOURCE_LABEL,
        _us(_share(us_share)),
        (f"Leading causes: {', '.join(causes)}. " if causes else "")
        + "States assess waters differently.",
    )


def _transit_factor(b: _Builder, transit_row: dict[str, Any]) -> None:
    stops = int(transit_row.get("stops") or 0)
    agencies = int(transit_row.get("agencies") or 0)
    value = (
        f"{_count(stops, 'stop', 'stops')} from {_count(agencies, 'agency', 'agencies')}"
        if stops
        else "None in the national transit map"
    )
    b.add(
        "transit_stops",
        "infrastructure",
        "Fixed-route public transit stops",
        value,
        transit.SOURCE_LABEL,
        detail=(
            "Covers buses and trains that publish a schedule feed. Dial-a-ride and other "
            "on-demand service, common in rural counties, is not included."
        ),
    )


def _profile_factors(b: _Builder, profile: list[ProfileFact]) -> None:
    """profile.py's figures (workforce and industries, social vulnerability,
    weather history, land cover and terrain) as factors, in the wording the
    panel shows."""
    for fact in profile:
        b.add(
            fact.id,
            PROFILE_CATEGORIES[fact.category],
            fact.label,
            format_profile_value(fact.value, fact.unit),
            fact.source,
            _us(format_profile_value(fact.national_value, fact.unit)),
            fact.note,
        )


def build_factors(
    *,
    acs: AcsMetrics | None,
    acs_national: AcsMetrics | None,
    energy: dict[str, Any] | None,
    nri_row: dict[str, Any] | None,
    solar_yield: float | None,
    farm_row: dict[str, Any] | None,
    ee_metrics: dict[str, Any] | None,
    assets: list[Asset],
    settings: Settings,
    profile: list[ProfileFact] | None = None,
    extra: CountyExtras | None = None,
) -> list[Factor]:
    """Every factor the loaded sources support, grouped by category."""
    x = extra or CountyExtras()
    b = _Builder()
    _climate_factors(b, solar_yield, nri_row, ee_metrics, settings)
    if x.disasters:
        _disaster_factor(b, x.disasters)
    if x.wildfire:
        _wildfire_factor(b, x.wildfire)
    if x.air:
        _air_factor(b, x.air, settings)
    if energy:
        _energy_factors(b, energy)
    if x.burden:
        _energy_burden_factor(b, x.burden)
    _utility_factors(b, x.utilities or [])
    _energy_community_factor(b, x.energy_community, x.energy_community_checked)
    if x.turbines:
        _turbine_factor(b, x.turbines)
    if acs:
        _acs_factors(b, acs, acs_national, settings.acs_label)
    _site_factors(b, assets)
    _plant_factor(b, assets)
    _water_system_factor(b, x.water_systems or [])
    if x.waters:
        _waters_factor(b, x.waters)
    if x.transit:
        _transit_factor(b, x.transit)
    if x.rucc:
        _rucc_factor(b, x.rucc)
    if farm_row:
        _farm_factors(b, farm_row)
    _profile_factors(b, profile or [])
    order = {category: i for i, category in enumerate(CATEGORY_ORDER)}
    return sorted(b.factors, key=lambda f: order[f.category])  # stable within a category
