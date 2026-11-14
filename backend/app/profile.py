"""County profile: what the county has to work with, as opposed to its needs.

Pure functions, like needs.py. A profile fact never flags a need or changes a
score. factors.py turns each one into a factor: it is shown in the Assets tab
and given to Gemini, which builds the opportunities from it (for example, a
large construction workforce makes a weatherization program easier to staff).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.models import ProfileCategory, ProfileFact, ProfileUnit
from app.sources import earthengine, openmeteo, svi
from app.sources.census import AcsMetrics


@dataclass(frozen=True)
class AcsProfileRule:
    id: str
    category: ProfileCategory
    label: str
    metric: str  # attribute of AcsMetrics
    unit: ProfileUnit
    note: str | None = None


ACS_PROFILE: tuple[AcsProfileRule, ...] = (
    AcsProfileRule(
        "trades_workers",
        "workforce",
        "Workers in construction, maintenance and natural resource jobs",
        "trades_occupation_share",
        "share",
        "Construction, extraction, installation, maintenance and repair, farming, fishing and "
        "forestry occupations, as a share of employed residents.",
    ),
    AcsProfileRule(
        "production_transport_workers",
        "workforce",
        "Workers in production, transportation and material moving jobs",
        "production_transport_occupation_share",
        "share",
    ),
    AcsProfileRule(
        "bachelors_or_higher",
        "workforce",
        "Adults with a bachelor's degree or higher",
        "bachelors_or_higher_share",
        "share",
        "Share of residents aged 25 and over.",
    ),
    AcsProfileRule(
        "less_than_high_school",
        "workforce",
        "Adults without a high school diploma",
        "less_than_high_school_share",
        "share",
        "Share of residents aged 25 and over.",
    ),
    AcsProfileRule(
        "agriculture_mining_jobs",
        "economy",
        "Jobs in agriculture, forestry, fishing and mining",
        "agriculture_mining_industry_share",
        "share",
        "Share of employed residents, by the industry they work in.",
    ),
    AcsProfileRule(
        "construction_jobs",
        "economy",
        "Jobs in construction",
        "construction_industry_share",
        "share",
        "Share of employed residents, by the industry they work in.",
    ),
    AcsProfileRule(
        "manufacturing_jobs",
        "economy",
        "Jobs in manufacturing",
        "manufacturing_industry_share",
        "share",
        "Share of employed residents, by the industry they work in.",
    ),
    AcsProfileRule(
        "transport_utilities_jobs",
        "economy",
        "Jobs in transportation, warehousing and utilities",
        "transport_utilities_industry_share",
        "share",
        "Share of employed residents, by the industry they work in.",
    ),
    AcsProfileRule("median_age", "community", "Median age", "median_age", "years"),
    AcsProfileRule(
        "no_broadband",
        "community",
        "Households without a broadband subscription",
        "no_broadband_share",
        "share",
        "Households with no internet subscription, or only dial-up or another service the "
        "Census does not count as broadband. Cellular data plans count as broadband.",
    ),
)


def acs_profile(
    county: AcsMetrics, national: AcsMetrics | None, settings: Settings
) -> list[ProfileFact]:
    """One fact per ACS profile figure the county has, beside the national
    figure when that is known."""
    facts: list[ProfileFact] = []
    for rule in ACS_PROFILE:
        value = getattr(county, rule.metric)
        if value is None:
            continue
        baseline = getattr(national, rule.metric) if national is not None else None
        facts.append(
            ProfileFact(
                id=rule.id,
                category=rule.category,
                label=rule.label,
                value=float(value),
                national_value=float(baseline) if baseline is not None else None,
                unit=rule.unit,
                source=settings.acs_label,
                note=rule.note,
            )
        )
    return facts


_YEARS = f"{openmeteo.FIRST_YEAR}-{openmeteo.LAST_YEAR}"
_POINT = (
    "Modelled weather for one point at the county's center, not a weather station; "
    "hills and valleys in the county can differ."
)

# (summary key, id, label, unit, note)
CLIMATE_PROFILE: tuple[tuple[str, str, str, ProfileUnit, str], ...] = (
    (
        "heating_degree_days",
        "heating_degree_days",
        "Heating degree days a year",
        "degree_days_f",
        f"How much heating homes need: the yearly sum of degrees the daily mean temperature "
        f"falls below 65 F, averaged over {_YEARS}. {_POINT}",
    ),
    (
        "cooling_degree_days",
        "cooling_degree_days",
        "Cooling degree days a year",
        "degree_days_f",
        f"How much cooling homes need: the yearly sum of degrees the daily mean temperature "
        f"rises above 65 F, averaged over {_YEARS}. {_POINT}",
    ),
    (
        "hot_days",
        "hot_days",
        "Days at 90 F or hotter a year",
        "days_per_year",
        f"Averaged over {_YEARS}. {_POINT}",
    ),
    (
        "precipitation_in",
        "precipitation",
        "Rain and snow (as water) a year",
        "inches_per_year",
        f"Averaged over {_YEARS}. {_POINT}",
    ),
    (
        "snowfall_in",
        "snowfall",
        "Snowfall a year",
        "inches_per_year",
        f"Averaged over {_YEARS}. {_POINT}",
    ),
    (
        "wind_speed_100m",
        "wind_speed_100m",
        "Average wind speed at 100 m (turbine height)",
        "m_per_s",
        f"Average over {openmeteo.WIND_YEAR}. A screening figure only: wind projects need "
        f"on-site measurement. {_POINT}",
    ),
)


def climate_profile(summary: dict[str, float] | None) -> list[ProfileFact]:
    """Open-Meteo has no national figure, so none is given."""
    if not summary:
        return []
    return [
        ProfileFact(
            id=fact_id,
            category="climate",
            label=label,
            value=float(summary[key]),
            unit=unit,
            source=f"{openmeteo.SOURCE_LABEL}, {_YEARS}",
            note=note,
        )
        for key, fact_id, label, unit, note in CLIMATE_PROFILE
        if summary.get(key) is not None
    ]


_RANK_NOTE = (
    "Percentile among all US counties: higher means more vulnerable. {what} Based on "
    "ACS 2018-2022 figures."
)

# (row key, id, label, unit, note)
SVI_PROFILE: tuple[tuple[str, str, str, ProfileUnit, str], ...] = (
    (
        "svi_overall",
        "svi_overall",
        "Social vulnerability, overall",
        "percentile",
        _RANK_NOTE.format(
            what="How hard it may be for residents to prepare for, cope with and recover from "
            "a disaster."
        ),
    ),
    (
        "svi_socioeconomic",
        "svi_socioeconomic",
        "Social vulnerability: income and jobs",
        "percentile",
        _RANK_NOTE.format(
            what="Poverty, unemployment, housing cost burden, schooling and health insurance."
        ),
    ),
    (
        "svi_household",
        "svi_household",
        "Social vulnerability: household make-up",
        "percentile",
        _RANK_NOTE.format(
            what="Older and younger residents, disability, single parents and limited English."
        ),
    ),
    (
        "svi_housing_transport",
        "svi_housing_transport",
        "Social vulnerability: housing and transportation",
        "percentile",
        _RANK_NOTE.format(
            what="Apartment buildings, mobile homes, crowding, households without a car and "
            "group quarters."
        ),
    ),
    (
        "age65_pct",
        "age_65_plus",
        "Residents aged 65 and over",
        "share",
        "Older residents are more at risk in heat waves and power outages.",
    ),
    (
        "disability_pct",
        "disability",
        "Residents with a disability",
        "share",
        "Share of the civilian population outside institutions.",
    ),
)


def svi_profile(row: dict[str, Any] | None) -> list[ProfileFact]:
    """SVI has no national row; a rank's national midpoint is 0.5 by definition,
    so ranks carry no national value and neither do the percentages."""
    if not row:
        return []
    facts: list[ProfileFact] = []
    for key, fact_id, label, unit, note in SVI_PROFILE:
        value = row.get(key)
        if value is None:
            continue
        facts.append(
            ProfileFact(
                id=fact_id,
                category="community",
                label=label,
                value=float(value) / 100 if unit == "share" else float(value),  # EP_* are 0-100
                unit=unit,
                source=svi.SOURCE_LABEL,
                note=note,
            )
        )
    return facts


FEET_PER_METER = 3.28084
_SHARE_OF_AREA = "Share of the county's land and water area."

# (metric key, id, label, unit, note)
LAND_PROFILE: tuple[tuple[str, str, str, ProfileUnit, str], ...] = (
    ("forest_share", "forest", "Forest", "share", _SHARE_OF_AREA),
    ("cropland_share", "cropland", "Cropland", "share", _SHARE_OF_AREA),
    ("grass_shrub_share", "grass_shrub", "Grassland and shrubland", "share", _SHARE_OF_AREA),
    (
        "built_up_share",
        "built_up",
        "Built-up land (buildings and pavement)",
        "share",
        _SHARE_OF_AREA,
    ),
    (
        "water_share",
        "open_water",
        "Open water (lakes, rivers, reservoirs)",
        "share",
        _SHARE_OF_AREA,
    ),
    ("wetland_share", "wetland", "Wetlands", "share", _SHARE_OF_AREA),
    (
        "flat_open_share",
        "flat_open_land",
        "Open land on gentle slopes",
        "share",
        f"Grass, shrubs, cropland or bare ground with a slope under "
        f"{earthengine.FLAT_SLOPE_DEGREES} degrees, as a share of county area: a first screen "
        "for land that could host ground-mounted projects. Ownership, protection, flooding "
        "and grid access are not checked.",
    ),
    (
        "elevation_mean_m",
        "elevation_mean",
        "Average elevation",
        "feet",
        "NASA SRTM. Not available north of 60 N.",
    ),
    (
        "elevation_range_m",
        "elevation_range",
        "Elevation range (highest minus lowest point)",
        "feet",
        "Large ranges mean hills or mountains, which matter for wind, small hydro and siting.",
    ),
    (
        "slope_deg",
        "slope_mean",
        "Average slope",
        "degrees",
        "Measured on a 240 m grid, which smooths out short steep slopes.",
    ),
)


def land_profile(metrics: dict[str, Any] | None) -> list[ProfileFact]:
    """Land cover and terrain from the Earth Engine metrics. No national figures."""
    if not metrics:
        return []
    values = dict(metrics)
    low, high = metrics.get("elevation_min_m"), metrics.get("elevation_max_m")
    values["elevation_range_m"] = high - low if low is not None and high is not None else None
    facts: list[ProfileFact] = []
    for key, fact_id, label, unit, note in LAND_PROFILE:
        value = values.get(key)
        if value is None:
            continue
        facts.append(
            ProfileFact(
                id=fact_id,
                category="geography",
                label=label,
                value=float(value) * FEET_PER_METER if unit == "feet" else float(value),
                unit=unit,
                source=earthengine.LAND_SOURCE_LABEL,
                note=note,
            )
        )
    return facts


def format_profile_value(value: float | None, unit: str) -> str | None:
    """The same text the frontend shows, so the Gemini number check passes."""
    if value is None:
        return None
    if unit == "share":
        return f"{value * 100:.1f}%"
    if unit == "years":
        return f"{value:.1f} years"
    if unit == "degree_days_f":
        return f"{value:,.0f} degree days"
    if unit == "days_per_year":
        days = f"{value:.0f}"
        return f"{days} {'day' if days == '1' else 'days'} a year"
    if unit == "inches_per_year":
        return f"{value:.0f} inches a year"
    if unit == "m_per_s":
        return f"{value:.1f} m/s"
    if unit == "feet":
        return f"{value:,.0f} feet"
    if unit == "degrees":
        return f"{value:.1f} degrees"
    if unit == "percentile":
        return f"{_ordinal(round(value * 100))} percentile"
    return None


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
