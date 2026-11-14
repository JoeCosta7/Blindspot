"""Labelled estimates (CLAUDE.md section 10).

Every constant below is an assumption. Each one is named in the `assumptions`
list of the estimate that uses it, so the user sees it. Do not add job or bill
savings multipliers without a cited source.
"""

from __future__ import annotations

import math
from typing import Any

from app.models import Estimate
from app.sources.pvwatts import LOSSES_PERCENT, TILT_DEGREES

# Land needed per MW of solar. Source: EPA RE-Powering Screening Dataset 2022,
# definition of "Estimated PV Capacity (MW)": "based on land requirement of
# 6.9 acres per MW (subject to a maximum value of 600 MW)". Read 2026-11-13.
ACRES_PER_MW = 6.9
MAX_SITE_MW = 600.0

# Share of a site assumed buildable. Our own planning assumption: EPA's figure
# above applies to the whole site, and contaminated sites usually have areas
# that cannot be built on.
# VERIFY: no published source for this value.
USABLE_FRACTION = 0.6

# Average annual electricity sold to a US residential customer, 2022.
# Source: EIA FAQ "How much electricity does an American home use?"
# https://www.eia.gov/tools/faqs/faq.php?id=97&t=3 (page dated 2024-01-08),
# read 2026-11-13. VERIFY: a newer year may be in EIA's Electric Sales,
# Revenue, and Average Price report, Table 5.a.
HOUSEHOLD_KWH_PER_YEAR = 10_791

# Illustrative rooftop array for a resilience hub. Not derived from the
# building: we have no roof area data.
RESILIENCE_HUB_KW = 100.0


def round_sig(value: float, digits: int = 3) -> float:
    """Round to significant figures so estimates do not imply false precision."""
    if value == 0 or not math.isfinite(value):
        return 0.0
    magnitude = math.floor(math.log10(abs(value)))
    return round(value, digits - 1 - magnitude)


def format_number(value: float) -> str:
    """1234567.0 -> "1,234,567"; 4.25 -> "4.25". Never exponent notation."""
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,}"


def _yield_assumption(site_specific: bool) -> str:
    where = "at the site" if site_specific else "at the county center, not the site"
    return (
        f"NLR PVWatts typical-year output {where}: fixed tilt {TILT_DEGREES}°, "
        f"facing south, {LOSSES_PERCENT}% system losses"
    )


def _energy_and_households(
    capacity_kw: float, yield_kwh_per_kw: float, site_specific: bool, basis: list[str]
) -> list[Estimate]:
    annual_kwh = capacity_kw * yield_kwh_per_kw
    energy_assumptions = [*basis, _yield_assumption(site_specific)]
    return [
        Estimate(
            label="Annual energy",
            value=round_sig(annual_kwh / 1000),
            unit="MWh per year",
            assumptions=energy_assumptions,
        ),
        Estimate(
            label="Household equivalent",
            value=round_sig(annual_kwh / HOUSEHOLD_KWH_PER_YEAR, 2),
            unit="homes",
            assumptions=[
                *energy_assumptions,
                f"Average US home buys {HOUSEHOLD_KWH_PER_YEAR:,} kWh a year (EIA, 2022)",
            ],
        ),
    ]


def solar_on_land(
    acres: float | None, yield_kwh_per_kw: float | None, site_specific_yield: bool
) -> list[Estimate]:
    """Capacity, and energy and households when a PVWatts yield is available.
    With no acreage there is nothing to compute, so no estimate is returned."""
    if acres is None or acres <= 0:
        return []
    capacity_mw = min(acres * USABLE_FRACTION / ACRES_PER_MW, MAX_SITE_MW)
    basis = [
        f"{USABLE_FRACTION:.0%} of the site's {format_number(round_sig(acres))} acres is usable",
        f"{ACRES_PER_MW} acres per MW (EPA RE-Powering screening figure)",
    ]
    if capacity_mw >= MAX_SITE_MW:
        basis.append(f"Capped at {MAX_SITE_MW:,.0f} MW, EPA's screening maximum")
    estimates = [
        Estimate(label="Capacity", value=round_sig(capacity_mw), unit="MW", assumptions=basis)
    ]
    if yield_kwh_per_kw:
        estimates += _energy_and_households(
            capacity_mw * 1000, yield_kwh_per_kw, site_specific_yield, basis
        )
    return estimates


def resilience_hub(yield_kwh_per_kw: float | None, site_specific_yield: bool) -> list[Estimate]:
    basis = [
        f"Illustrative {RESILIENCE_HUB_KW:,.0f} kW rooftop array; the building's roof "
        "has not been measured"
    ]
    estimates = [Estimate(label="Capacity", value=RESILIENCE_HUB_KW, unit="kW", assumptions=basis)]
    if yield_kwh_per_kw:
        estimates += _energy_and_households(
            RESILIENCE_HUB_KW, yield_kwh_per_kw, site_specific_yield, basis
        )
    return estimates


def landfill_gas(attributes: dict[str, Any]) -> list[Estimate]:
    """Figures EPA reports for the landfill. Nothing is derived: converting gas
    flow to power needs engine and methane assumptions we have no source for."""
    estimates: list[Estimate] = []
    collected = attributes.get("lfg_collected_mmscfd")
    generated = attributes.get("lfg_generated_mmscfd")
    waste = attributes.get("waste_in_place_tons")
    if collected:
        year = attributes.get("lfg_collected_year")
        when = f" for {int(year)}" if year else ""
        estimates.append(
            Estimate(
                label="Landfill gas collected",
                value=round_sig(float(collected)),
                unit="million cubic feet per day",
                assumptions=[f"Reported in the EPA LMOP database{when}; not checked by this tool"],
            )
        )
    if generated:
        estimates.append(
            Estimate(
                label="Landfill gas generated",
                value=round_sig(float(generated)),
                unit="million cubic feet per day",
                assumptions=["EPA LMOP database estimate of gas generated by the landfill"],
            )
        )
    if waste:
        estimates.append(
            Estimate(
                label="Waste in place",
                value=round_sig(float(waste)),
                unit="tons",
                assumptions=["Reported in the EPA LMOP database; not checked by this tool"],
            )
        )
    return estimates


def has_estimate_inputs(asset_kind: str, attributes: dict[str, Any]) -> bool:
    """Whether the asset carries the data its estimate needs. Used as the
    asset factor in matching."""
    if asset_kind == "land":
        acres = attributes.get("acres")
        return acres is not None and acres > 0
    if asset_kind == "landfill":
        return any(
            attributes.get(k)
            for k in ("lfg_collected_mmscfd", "lfg_generated_mmscfd", "waste_in_place_tons")
        )
    return True  # buildings: the estimate does not depend on the building
