"""Assets, factors, knowledge: everything about a county that a solution
could draw on.

A need says what is wrong. A factor says what the county has to work with:
its climate, land, buildings, workforce, economy, energy system,
infrastructure and farms. Factors are county-level facts; individual places
are the mapped assets. Gemini is given both, with the needs, and writes the
opportunities from them (opportunities.py).

Pure functions. A factor is listed only when its source loaded; nothing here
is estimated, and every figure comes straight from a source or is a count or
sum of what the source lists.
"""

from __future__ import annotations

import math
from typing import Any

from app.config import Settings
from app.models import Asset, Factor, FactorCategory
from app.sources import (
    afdc,
    dams,
    earthengine,
    farms,
    geoapify,
    lmop,
    nri,
    repowering,
    state_energy,
)
from app.sources.census import AcsMetrics

PVWATTS_LABEL = "NLR PVWatts v8"

# The order the panel and the prompt list them in.
CATEGORY_ORDER: tuple[FactorCategory, ...] = (
    "climate",
    "energy",
    "land",
    "buildings",
    "workforce",
    "economy",
    "infrastructure",
    "agriculture",
)

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

    # Workforce: the county's local talent, and who is free to be hired.
    share(
        "unemployment",
        "workforce",
        "Unemployment rate",
        "unemployment_rate",
        "Share of the civilian labor force that is out of work and looking for it.",
    )
    share(
        "construction_workforce",
        "workforce",
        "Employed residents working in construction",
        "construction_work_share",
    )
    share(
        "manufacturing_workforce",
        "workforce",
        "Employed residents working in manufacturing",
        "manufacturing_work_share",
    )
    share(
        "farm_forest_mining_workforce",
        "workforce",
        "Employed residents working in farming, forestry, fishing or mining",
        "farm_forest_mining_work_share",
        "One category in the census; it cannot be split further.",
    )
    share(
        "college_degrees",
        "workforce",
        "Adults 25 and over with a bachelor's degree or higher",
        "bachelors_or_higher_share",
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
) -> list[Factor]:
    """Every factor the loaded sources support, grouped by category."""
    b = _Builder()
    _climate_factors(b, solar_yield, nri_row, ee_metrics, settings)
    if energy:
        _energy_factors(b, energy)
    if acs:
        _acs_factors(b, acs, acs_national, settings.acs_label)
    _site_factors(b, assets)
    if farm_row:
        _farm_factors(b, farm_row)
    order = {category: i for i, category in enumerate(CATEGORY_ORDER)}
    return sorted(b.factors, key=lambda f: order[f.category])  # stable within a category
