"""Census ACS 5-year: county figures and the national baseline.

Variable codes were confirmed against
https://api.census.gov/data/2024/acs/acs5/variables.json on 2026-11-13.
The API has required a key since at least that date: keyless calls get a 302
to /data/missing_key.html.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import httpx

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.sources import SourceError

BASE_URL = "https://api.census.gov/data/{year}/acs/acs5"

VARIABLES: list[str] = [
    "B01003_001E",  # Total population
    "B19013_001E",  # Median household income, past 12 months
    "B17001_001E",  # Poverty status: total
    "B17001_002E",  # Poverty status: income below poverty level
    "B23025_003E",  # Civilian labor force
    "B23025_005E",  # Civilian labor force: unemployed
    "B25034_001E",  # Year structure built: total
    "B25034_007E",  # Built 1970 to 1979
    "B25034_008E",  # Built 1960 to 1969
    "B25034_009E",  # Built 1950 to 1959
    "B25034_010E",  # Built 1940 to 1949
    "B25034_011E",  # Built 1939 or earlier
    "B25040_001E",  # House heating fuel: total
    "B25040_003E",  # Bottled or tank gas (propane, butane, etc.)
    "B25040_004E",  # Electricity
    "B25040_005E",  # Fuel oil, kerosene, etc.
    "B25024_001E",  # Units in structure: total
    "B25024_010E",  # Mobile home
    "B25003_001E",  # Tenure: total
    "B25003_003E",  # Renter occupied
    "B08201_001E",  # Household size by vehicles available: total
    "B08201_002E",  # No vehicle available
    "B08301_001E",  # Means of transportation to work: total
    "B08301_003E",  # Car, truck or van: drove alone
    "B08303_001E",  # Travel time to work: total
    "B08303_011E",  # 45 to 59 minutes
    "B08303_012E",  # 60 to 89 minutes
    "B08303_013E",  # 90 or more minutes
]
assert len(VARIABLES) <= 49  # the API allows 50 per call, NAME included

Values = dict[str, float | None]


@dataclass(frozen=True)
class AcsMetrics:
    population: int | None
    median_household_income: float | None
    poverty_rate: float | None
    unemployment_rate: float | None
    pre1980_housing_share: float | None
    delivered_fuel_heat_share: float | None
    electric_heat_share: float | None
    mobile_home_share: float | None
    renter_share: float | None
    no_vehicle_share: float | None
    drive_alone_share: float | None
    long_commute_share: float | None


def parse_response(body: list[list[str | None]]) -> Values:
    """The API returns [header, row]. Negative sentinels such as -666666666
    mean "no data" and become None."""
    if len(body) < 2:
        raise SourceError("Census returned no rows for this geography")
    header, row = body[0], body[1]
    values: Values = {}
    for name, raw in zip(header, row, strict=True):
        if name not in VARIABLES:
            continue
        try:
            number = float(raw) if raw is not None else None
        except ValueError:
            number = None
        values[name] = None if number is None or number < 0 else number
    return values


def _sum(values: Values, names: list[str]) -> float | None:
    parts = [values.get(n) for n in names]
    if any(p is None for p in parts):
        return None
    return float(sum(parts))  # type: ignore[arg-type]


def _share(values: Values, numerators: list[str], denominator: str) -> float | None:
    top = _sum(values, numerators)
    bottom = values.get(denominator)
    if top is None or not bottom:
        return None
    return top / bottom


def metrics_from_values(v: Values) -> AcsMetrics:
    population = v.get("B01003_001E")
    return AcsMetrics(
        population=int(population) if population is not None else None,
        median_household_income=v.get("B19013_001E"),
        poverty_rate=_share(v, ["B17001_002E"], "B17001_001E"),
        unemployment_rate=_share(v, ["B23025_005E"], "B23025_003E"),
        pre1980_housing_share=_share(
            v,
            ["B25034_007E", "B25034_008E", "B25034_009E", "B25034_010E", "B25034_011E"],
            "B25034_001E",
        ),
        delivered_fuel_heat_share=_share(v, ["B25040_003E", "B25040_005E"], "B25040_001E"),
        electric_heat_share=_share(v, ["B25040_004E"], "B25040_001E"),
        mobile_home_share=_share(v, ["B25024_010E"], "B25024_001E"),
        renter_share=_share(v, ["B25003_003E"], "B25003_001E"),
        no_vehicle_share=_share(v, ["B08201_002E"], "B08201_001E"),
        drive_alone_share=_share(v, ["B08301_003E"], "B08301_001E"),
        long_commute_share=_share(v, ["B08303_011E", "B08303_012E", "B08303_013E"], "B08303_001E"),
    )


async def _fetch(client: httpx.AsyncClient, settings: Settings, geo: dict[str, str]) -> Values:
    if not settings.census_api_key:
        raise SourceError("CENSUS_API_KEY is not set (the Census API rejects keyless calls)")
    params = {"get": "NAME," + ",".join(VARIABLES), **geo, "key": settings.census_api_key}
    try:
        resp = await client.get(
            BASE_URL.format(year=settings.acs_year),
            params=params,
            timeout=settings.source_timeout_s,
        )
    except httpx.HTTPError as exc:
        raise SourceError(f"Census request failed: {type(exc).__name__}") from exc
    if resp.status_code == 302:
        raise SourceError("Census rejected the API key")
    if resp.status_code == 204:
        raise SourceError("Census has no ACS data for this geography")
    if resp.status_code != 200:
        raise SourceError(f"Census returned HTTP {resp.status_code}")
    try:
        return parse_response(resp.json())
    except ValueError as exc:
        raise SourceError("Census returned a response that is not JSON") from exc


async def fetch_county(
    client: httpx.AsyncClient, settings: Settings, state_fips: str, county_code: str
) -> tuple[AcsMetrics, bool]:
    values, cached = await cached_fetch(
        settings.cache_dir,
        "census_county",
        {"year": settings.acs_year, "state": state_fips, "county": county_code},
        30 * DAY,
        lambda: _fetch(
            client, settings, {"for": f"county:{county_code}", "in": f"state:{state_fips}"}
        ),
    )
    return metrics_from_values(values), cached


async def fetch_national(client: httpx.AsyncClient, settings: Settings) -> tuple[AcsMetrics, bool]:
    values, cached = await cached_fetch(
        settings.cache_dir,
        "census_national",
        {"year": settings.acs_year},
        30 * DAY,
        lambda: _fetch(client, settings, {"for": "us:1"}),
    )
    return metrics_from_values(values), cached


def metrics_as_dict(metrics: AcsMetrics) -> dict[str, float | int | None]:
    return asdict(metrics)
