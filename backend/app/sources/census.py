"""Census ACS 5-year: county figures and the national baseline.

Variable codes were confirmed against
https://api.census.gov/data/2024/acs/acs5/variables.json on 2026-11-13
(C24050, B15003 and B25002 on 2026-11-14).
The API has required a key since at least that date: keyless calls get a 302
to /data/missing_key.html.
"""

from __future__ import annotations

import hashlib
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
    # Profile: what the county has to work with. Confirmed against
    # https://api.census.gov/data/2024/acs/acs5/groups/<table>.json on 2026-11-14.
    "B01002_001E",  # Median age: total
    "B06009_001E",  # Educational attainment, population 25+: total
    "B06009_002E",  # Less than high school graduate
    "B06009_005E",  # Bachelor's degree
    "B06009_006E",  # Graduate or professional degree
    "B28002_001E",  # Internet subscriptions in household: total
    "B28002_004E",  # With an Internet subscription: broadband of any type
    # C24050: industry and occupation, civilian employed population 16+. Unlike
    # C24010 and C24030 it has totals that are not split by sex.
    "C24050_001E",  # Total
    "C24050_002E",  # Industry: agriculture, forestry, fishing and hunting, and mining
    "C24050_003E",  # Industry: construction
    "C24050_004E",  # Industry: manufacturing
    "C24050_007E",  # Industry: transportation and warehousing, and utilities
    "C24050_057E",  # Occupation: natural resources, construction, and maintenance
    "C24050_071E",  # Occupation: production, transportation, and material moving
    # Housing stock, for factors.py. Confirmed the same way on 2026-11-14.
    "B25002_001E",  # Occupancy status: total housing units
    "B25002_003E",  # Vacant
]
assert len(VARIABLES) <= 49  # the API allows 50 per call, NAME included
# In every cache key, so adding a variable does not serve 30-day-old rows without it.
VARIABLES_KEY = hashlib.sha256(",".join(VARIABLES).encode()).hexdigest()[:12]

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
    # Profile figures. Defaults keep older callers and tests working.
    median_age: float | None = None
    less_than_high_school_share: float | None = None  # of adults 25+
    bachelors_or_higher_share: float | None = None  # of adults 25+
    no_broadband_share: float | None = None  # of households
    # Shares of the civilian employed population.
    trades_occupation_share: float | None = None
    production_transport_occupation_share: float | None = None
    agriculture_mining_industry_share: float | None = None
    construction_industry_share: float | None = None
    manufacturing_industry_share: float | None = None
    transport_utilities_industry_share: float | None = None
    # Housing stock, for factors.py.
    housing_units: int | None = None
    vacant_housing_share: float | None = None


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
    housing_units = v.get("B25002_001E")
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
        median_age=v.get("B01002_001E"),
        less_than_high_school_share=_share(v, ["B06009_002E"], "B06009_001E"),
        bachelors_or_higher_share=_share(v, ["B06009_005E", "B06009_006E"], "B06009_001E"),
        no_broadband_share=_complement(_share(v, ["B28002_004E"], "B28002_001E")),
        trades_occupation_share=_share(v, ["C24050_057E"], "C24050_001E"),
        production_transport_occupation_share=_share(v, ["C24050_071E"], "C24050_001E"),
        agriculture_mining_industry_share=_share(v, ["C24050_002E"], "C24050_001E"),
        construction_industry_share=_share(v, ["C24050_003E"], "C24050_001E"),
        manufacturing_industry_share=_share(v, ["C24050_004E"], "C24050_001E"),
        transport_utilities_industry_share=_share(v, ["C24050_007E"], "C24050_001E"),
        housing_units=int(housing_units) if housing_units is not None else None,
        vacant_housing_share=_share(v, ["B25002_003E"], "B25002_001E"),
    )


def _complement(share: float | None) -> float | None:
    return None if share is None else 1.0 - share


async def _request(
    client: httpx.AsyncClient, settings: Settings, geo: dict[str, str]
) -> list[list[str | None]]:
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
        return resp.json()
    except ValueError as exc:
        raise SourceError("Census returned a response that is not JSON") from exc


async def _fetch(client: httpx.AsyncClient, settings: Settings, geo: dict[str, str]) -> Values:
    return parse_response(await _request(client, settings, geo))


def parse_all_counties(body: list[list[str | None]]) -> dict[str, Values]:
    """Every county's values by 5-digit FIPS, from a `for=county:*` response,
    whose rows end with the state and county codes."""
    if len(body) < 2:
        raise SourceError("Census returned no county rows")
    header = body[0]
    try:
        state_at, county_at = header.index("state"), header.index("county")
    except ValueError as exc:
        raise SourceError("Census county rows carry no state and county codes") from exc
    return {f"{row[state_at]}{row[county_at]}": parse_response([header, row]) for row in body[1:]}


async def fetch_all_counties(
    client: httpx.AsyncClient, settings: Settings
) -> tuple[dict[str, Values], bool]:
    """The same variables for every US county in one call (about 1 MB; checked
    2026-11-14: 3,222 rows in 2.5 s). Used to find peer counties."""

    async def fetch() -> dict[str, Values]:
        return parse_all_counties(await _request(client, settings, {"for": "county:*"}))

    return await cached_fetch(
        settings.cache_dir,
        "census_all_counties",
        {"year": settings.acs_year, "vars": VARIABLES_KEY},
        30 * DAY,
        fetch,
    )


async def fetch_county(
    client: httpx.AsyncClient, settings: Settings, state_fips: str, county_code: str
) -> tuple[AcsMetrics, bool]:
    values, cached = await cached_fetch(
        settings.cache_dir,
        "census_county",
        {
            "year": settings.acs_year,
            "state": state_fips,
            "county": county_code,
            "vars": VARIABLES_KEY,
        },
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
        {"year": settings.acs_year, "vars": VARIABLES_KEY},
        30 * DAY,
        lambda: _fetch(client, settings, {"for": "us:1"}),
    )
    return metrics_from_values(values), cached


def metrics_as_dict(metrics: AcsMetrics) -> dict[str, float | int | None]:
    return asdict(metrics)
