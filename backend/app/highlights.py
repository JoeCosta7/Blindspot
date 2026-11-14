"""Highlights: the few things a county has that stand out for the better.

Two kinds of comparison, both from data already loaded:

1. A factor with a US figure (workforce shares, electric heat, electricity
   price, grid carbon, broadband, tree canopy), where it is clear which
   direction is good. ratio = county / US, or US / county when lower is better.
2. Reclaimed land and farmland, against the average US county: the national
   total in the same prepared file divided by the number of counties in
   counties.geojson.

A factor is kept when the ratio reaches `highlight_min_ratio`; the best
`max_highlights` are returned. The "why" lines are fixed text with no numbers.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import Settings
from app.models import Asset, Factor, Highlight, ProfileFact
from app.sources.census import AcsMetrics

# Profile facts where a higher county share is a strength, and why.
HIGHER_IS_BETTER: dict[str, str] = {
    "trades_workers": "Local trades workers who can build and maintain energy projects.",
    "construction_jobs": "A construction sector that can take on local projects.",
    "production_transport_workers": "Production and logistics workers for plant operations.",
    "manufacturing_jobs": "A manufacturing base that clean-energy suppliers look for.",
    "transport_utilities_jobs": "Utility and transport workers who run energy infrastructure.",
    "agriculture_mining_jobs": "Workers used to heavy equipment and site work on land.",
    "bachelors_or_higher": "A college-educated workforce for planning and technical roles.",
}
# Profile facts where a lower county share is a strength.
LOWER_IS_BETTER: dict[str, str] = {
    "no_broadband": "Good home internet coverage for smart-meter and remote-work programs.",
}

LAND_WHY: dict[str, str] = {
    "mine_land": "Large reclaimed mine sites, already screened by EPA for solar and wind.",
    "brownfield": "Brownfield acreage that EPA grants can help clean up for reuse.",
    "landfill": "Landfill acreage that can host solar once closed.",
    "contaminated_site": "Cleanup sites screened by EPA for renewable energy.",
}


@lru_cache(maxsize=4)
def _county_count(data_dir: Path) -> int:
    with open(data_dir / "counties.geojson", encoding="utf-8") as f:
        return len(json.load(f)["features"])


@lru_cache(maxsize=4)
def _average_county(data_dir: Path) -> dict[str, float]:
    """Average acres per US county: RE-Powering site types and farmland."""
    counties = _county_count(data_dir)
    sites = pd.read_parquet(data_dir / "repowering.parquet", columns=["site_type", "acres"])
    averages = {
        f"sites_{site_type}": float(acres) / counties
        for site_type, acres in sites.groupby("site_type")["acres"].sum().items()
    }
    farms = pd.read_parquet(data_dir / "farms.parquet", columns=["land_in_farms_acres"])
    averages["farmland"] = float(farms["land_in_farms_acres"].sum()) / counties
    return averages


def _ratio(value: float | None, benchmark: float | None, higher_better: bool) -> float | None:
    if value is None or benchmark is None or not (value > 0 and benchmark > 0):
        return None
    ratio = value / benchmark if higher_better else benchmark / value
    return ratio if math.isfinite(ratio) else None


def find_highlights(
    *,
    factors: list[Factor],
    profile: list[ProfileFact],
    acs: AcsMetrics | None,
    acs_national: AcsMetrics | None,
    energy: dict[str, Any] | None,
    ee_metrics: dict[str, Any] | None,
    farm_row: dict[str, Any] | None,
    assets: list[Asset],
    settings: Settings,
    averages: Callable[[Path], dict[str, float]] = _average_county,
) -> list[Highlight]:
    by_id = {f.id: f for f in factors}
    # factor id -> (ratio, benchmark text or None to use the factor's, why)
    found: dict[str, tuple[float, str | None, str]] = {}

    def consider(factor_id: str, ratio: float | None, why: str, benchmark: str | None = None):
        if factor_id in by_id and ratio is not None and ratio >= settings.highlight_min_ratio:
            found[factor_id] = (ratio, benchmark, why)

    for fact in profile:
        if fact.id in HIGHER_IS_BETTER:
            consider(
                fact.id, _ratio(fact.value, fact.national_value, True), HIGHER_IS_BETTER[fact.id]
            )
        elif fact.id in LOWER_IS_BETTER:
            consider(
                fact.id, _ratio(fact.value, fact.national_value, False), LOWER_IS_BETTER[fact.id]
            )

    if acs and acs_national:
        consider(
            "electric_heat",
            _ratio(acs.electric_heat_share, acs_national.electric_heat_share, True),
            "Many homes already heat with electricity, so heat pumps are a direct swap.",
        )
    if energy:
        state, us = energy["state"], energy["national"]
        consider(
            "electricity_price",
            _ratio(
                state.get("residential_price_cents_kwh"),
                us.get("residential_price_cents_kwh"),
                False,
            ),
            "Cheaper electricity than the US average, which helps electrification pay off.",
        )
        consider(
            "grid_carbon",
            _ratio(state.get("grid_co2_lb_per_mwh"), us.get("grid_co2_lb_per_mwh"), False),
            "A cleaner grid, so switching heating and cars to electricity cuts more carbon.",
        )
    if ee_metrics:
        consider(
            "tree_canopy",
            _ratio(ee_metrics.get("canopy_built_up"), settings.tree_canopy_built_up_national, True),
            "More shade over streets and buildings than most US towns.",
        )

    try:
        average = averages(settings.data_dir)
    except (OSError, ValueError, KeyError):
        average = {}
    for site_type, why in LAND_WHY.items():
        factor_id = f"sites_{site_type}"
        acres = sum(
            float(a.attributes["acres"])
            for a in assets
            if a.type == site_type and isinstance(a.attributes.get("acres"), (int, float))
        )
        avg = average.get(factor_id)
        if avg:
            consider(
                factor_id, _ratio(acres, avg, True), why, f"Average US county: {avg:,.0f} acres"
            )
    if farm_row and average.get("farmland"):
        avg = average["farmland"]
        consider(
            "farmland",
            _ratio(farm_row.get("land_in_farms_acres"), avg, True),
            "Plenty of farmland for agrivoltaics, digesters and land-based projects.",
            f"Average US county: {avg:,.0f} acres in farms",
        )

    ranked = sorted(found.items(), key=lambda item: -item[1][0])[: settings.max_highlights]
    return [
        Highlight(
            factor_id=factor_id,
            label=by_id[factor_id].label,
            value=by_id[factor_id].value,
            benchmark=benchmark or by_id[factor_id].comparison or "",
            times_better=round(ratio, 1),
            why=why,
        )
        for factor_id, (ratio, benchmark, why) in ranked
    ]
