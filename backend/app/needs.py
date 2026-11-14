"""Rule-based need flags and severity (CLAUDE.md section 8). Pure functions:
no I/O, so every rule is unit-tested."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.models import Need, NeedLevel
from app.sources import afdc, farms, lmop, nri, state_energy
from app.sources.census import AcsMetrics


@dataclass(frozen=True)
class AcsNeedRule:
    id: str
    label: str
    theme: str
    metric: str  # attribute of AcsMetrics
    unit: str
    lower_is_worse: bool = False


ACS_RULES: tuple[AcsNeedRule, ...] = (
    AcsNeedRule(
        "low_income", "Low household income", "equity", "median_household_income", "usd", True
    ),
    AcsNeedRule(
        "old_housing", "Housing built before 1980", "energy", "pre1980_housing_share", "share"
    ),
    AcsNeedRule(
        "delivered_fuel_heat",
        "Homes heated with fuel oil or propane",
        "energy",
        "delivered_fuel_heat_share",
        "share",
    ),
    AcsNeedRule(
        "mobile_homes", "Mobile and manufactured homes", "energy", "mobile_home_share", "share"
    ),
    AcsNeedRule("renters", "Renter households", "equity", "renter_share", "share"),
    AcsNeedRule(
        "no_vehicle", "Households without a car", "transportation", "no_vehicle_share", "share"
    ),
    AcsNeedRule(
        "long_commute",
        "Commutes of 45 minutes or more",
        "transportation",
        "long_commute_share",
        "share",
    ),
    AcsNeedRule(
        "drive_alone", "Drive-alone commuting", "transportation", "drive_alone_share", "share"
    ),
)


def score_ratio(ratio: float, settings: Settings) -> tuple[int, NeedLevel | None]:
    """Severity 0-100 and level for a county-to-national ratio, where a ratio
    above 1 means the county is worse off."""
    scaled = (ratio - 1.0) / settings.need_severity_span
    severity = round(100 * min(max(scaled, 0.0), 1.0))
    level: NeedLevel | None
    if ratio >= settings.need_ratio_high:
        level = "high"
    elif ratio >= settings.need_ratio_elevated:
        level = "elevated"
    else:
        level = None
    return severity, level


def acs_needs(county: AcsMetrics, national: AcsMetrics, settings: Settings) -> list[Need]:
    needs: list[Need] = []
    for rule in ACS_RULES:
        value = getattr(county, rule.metric)
        baseline = getattr(national, rule.metric)
        if value is None or baseline is None or baseline <= 0:
            continue
        if rule.lower_is_worse:
            if value <= 0:
                continue
            ratio = baseline / value
        else:
            ratio = value / baseline
        severity, level = score_ratio(ratio, settings)
        if level is None:
            continue
        needs.append(
            Need(
                id=rule.id,  # type: ignore[arg-type]
                label=rule.label,
                theme=rule.theme,  # type: ignore[arg-type]
                value=float(value),
                national_value=float(baseline),
                unit=rule.unit,  # type: ignore[arg-type]
                severity=severity,
                level=level,
                source=settings.acs_label,
            )
        )
    return needs


def ev_benchmark_per_10k(
    national_stations: int | None, national_population: int | None, settings: Settings
) -> float | None:
    """Public EV stations per 10,000 residents nationally."""
    if settings.ev_benchmark_per_10k is not None:
        return settings.ev_benchmark_per_10k
    if not national_stations or not national_population:
        return None
    return national_stations / national_population * 10_000


def ev_need(
    station_count: int,
    population: int | None,
    benchmark_per_10k: float | None,
    settings: Settings,
) -> Need | None:
    """`high` when the county has no public stations. Otherwise the county's
    stations per 10,000 residents are compared to the national benchmark; with
    no benchmark or population there is nothing to compare, so no flag."""
    common = {
        "id": "ev_charging_gap",
        "label": "Few or no public EV chargers",
        "theme": "transportation",
        "unit": "per_10k_residents",
        "source": afdc.SOURCE_LABEL,
    }
    if station_count == 0:
        return Need(
            **common,
            value=0.0,
            national_value=benchmark_per_10k,
            severity=settings.severity_very_high,
            level="high",
            detail="No public charging stations were found in the county.",
        )
    if not population or not benchmark_per_10k:
        return None
    rate = station_count / population * 10_000
    severity, level = score_ratio(benchmark_per_10k / rate, settings)
    if level is None:
        return None
    plural = "station" if station_count == 1 else "stations"
    return Need(
        **common,
        value=rate,
        national_value=benchmark_per_10k,
        severity=severity,
        level=level,
        detail=f"{station_count} public charging {plural} in the county.",
    )


def landfill_need(landfills: list[dict[str, Any]], settings: Settings) -> Need | None:
    """`high` when the county has a landfill EPA lists as a candidate for a gas
    energy project (no operational, under-construction or planned project)."""
    candidates = [row for row in landfills if row.get("is_candidate")]
    if not candidates:
        return None
    names = sorted(str(row["name"]) for row in candidates if row.get("name"))
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    return Need(
        id="landfill_gas_uncaptured",
        label="Landfill with no gas energy project",
        theme="waste",
        value=float(len(candidates)),
        national_value=None,
        unit="count",
        severity=settings.severity_high,
        level="high",
        source=lmop.SOURCE_LABEL,
        detail=f"EPA candidate landfill: {shown}." if shown else None,
    )


def hazard_need(ratings: dict[str, Any] | None, settings: Settings) -> Need | None:
    """`high` if any tracked hazard is rated "Relatively High" or "Very High";
    `elevated` for "Relatively Moderate". The detail names each such hazard."""
    if not ratings:
        return None
    severity_by_rating = {
        "Very High": settings.severity_very_high,
        "Relatively High": settings.severity_high,
        "Relatively Moderate": settings.severity_elevated,
    }
    flagged: list[tuple[int, str, str]] = []
    for column, hazard in nri.HAZARDS.items():
        rating = ratings.get(column)
        if rating in severity_by_rating:
            flagged.append((severity_by_rating[rating], hazard, str(rating)))
    if not flagged:
        return None
    flagged.sort(key=lambda f: (-f[0], f[1]))
    severity = flagged[0][0]
    return Need(
        id="hazard_risk",
        label="High natural hazard risk",
        theme="resilience",
        value=None,
        national_value=None,
        unit="rating",
        severity=severity,
        level="high" if severity >= settings.severity_high else "elevated",
        source=nri.source_label(ratings.get("nri_version")),
        detail="; ".join(f"{hazard}: {rating}" for _, hazard, rating in flagged),
    )


def electricity_cost_need(energy: dict[str, Any] | None, settings: Settings) -> Need | None:
    """Average residential monthly electric bill in the county's state against
    the US average, scored like an ACS need. EIA does not publish it by county."""
    if not energy:
        return None
    bill = energy["state"].get("residential_monthly_bill_usd")
    baseline = energy["national"].get("residential_monthly_bill_usd")
    if not bill or not baseline:
        return None
    severity, level = score_ratio(bill / baseline, settings)
    if level is None:
        return None
    price = energy["state"].get("residential_price_cents_kwh")
    us_price = energy["national"].get("residential_price_cents_kwh")
    detail = f"State average for {energy['state_abbr']}; EIA does not publish county figures."
    if price and us_price:
        detail += f" The state's price is {price} cents per kWh, against {us_price} nationally."
    return Need(
        id="high_electricity_cost",
        label="High household electricity bills",
        theme="energy",
        value=float(bill),
        national_value=float(baseline),
        unit="usd_per_month",
        severity=severity,
        level=level,
        source=state_energy.bill_source_label(energy["electricity_year"]),
        detail=detail,
    )


def grid_carbon_need(energy: dict[str, Any] | None, settings: Settings) -> Need | None:
    """CO2 per MWh generated in the county's state against the US rate, scored
    like an ACS need."""
    if not energy:
        return None
    rate = energy["state"].get("grid_co2_lb_per_mwh")
    baseline = energy["national"].get("grid_co2_lb_per_mwh")
    if not rate or not baseline:
        return None
    severity, level = score_ratio(rate / baseline, settings)
    if level is None:
        return None
    return Need(
        id="carbon_intensive_grid",
        label="Carbon-intensive electricity",
        theme="energy",
        value=float(rate),
        national_value=float(baseline),
        unit="lb_co2_per_mwh",
        severity=severity,
        level=level,
        source=state_energy.grid_source_label(energy["grid_year"]),
        detail=(
            f"State average for {energy['state_abbr']}: carbon dioxide per megawatt-hour "
            "generated in the state. Power crosses state lines, so this is not exactly what "
            "the county's homes use."
        ),
    )


def _count_phrase(count: int, singular: str, plural: str) -> str:
    return f"{count:,} {singular if count == 1 else plural}"


def farm_waste_need(farm_row: dict[str, Any] | None, settings: Settings) -> Need | None:
    """Large livestock farms with fewer operating manure digesters than farms.

    EPA AgSTAR's screening size for a digester is 500 or more dairy cows or
    2,000 or more hogs (https://www.epa.gov/agstar/agstar-market-opportunities-report).
    The farm census publishes a 500-cow class by county but only a 1,000-hog
    class, so: `high` when the county has a 500-cow dairy and no operating
    digester; `elevated` when it has only 1,000-hog farms, or has some
    digesters but fewer than large farms.
    """
    if not farm_row:
        return None
    dairies = int(farm_row.get("dairy_farms_500_plus") or 0)
    hog_farms = int(farm_row.get("hog_farms_1000_plus") or 0)
    digesters = int(farm_row.get("digesters_operational") or 0)
    building = int(farm_row.get("digesters_under_construction") or 0)
    large = dairies + hog_farms
    if large == 0 or digesters >= large:
        return None
    high = dairies > 0 and digesters == 0

    found = []
    if dairies:
        found.append(_count_phrase(dairies, "dairy farm", "dairy farms") + " with 500 or more cows")
    if hog_farms:
        found.append(_count_phrase(hog_farms, "hog farm", "hog farms") + " with 1,000 or more hogs")
    detail = f"{' and '.join(found)} ({farms.CENSUS_LABEL}). "
    if digesters:
        detail += (
            f"EPA lists {_count_phrase(digesters, 'operating digester', 'operating digesters')}"
        )
    else:
        detail += "EPA lists no operating manure digester"
    detail += (
        f" in the county, and {building} under construction." if building else " in the county."
    )
    detail += " EPA's screening size for a digester is 500 cows or 2,000 hogs"
    if hog_farms:
        detail += "; the census has no 2,000-hog class by county"
    detail += "."
    return Need(
        id="unmanaged_farm_waste",
        label="Large livestock farms without manure digesters",
        theme="waste",
        value=float(large),
        national_value=None,
        unit="count",
        severity=settings.severity_high if high else settings.severity_elevated,
        level="high" if high else "elevated",
        source=farms.SOURCE_LABEL,
        detail=detail,
    )
