"""Source parsing and HTTP error handling, with no real network calls."""

import asyncio

import httpx
import pytest

from app import cache
from app.sources import SourceError, afdc, census, overpass, pvwatts


def client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- census -------------------------------------------------------------------


def census_body(overrides: dict[str, str] | None = None) -> list[list[str]]:
    values = {name: "100" for name in census.VARIABLES}
    values.update(overrides or {})
    header = ["NAME", *values, "state", "county"]
    return [header, ["Test County", *values.values(), "36", "007"]]


def test_census_metrics_formulas():
    body = census_body(
        {
            "B01003_001E": "50000",
            "B19013_001E": "61000",
            "B17001_001E": "1000",
            "B17001_002E": "150",
            "B25034_001E": "1000",
            "B25034_007E": "100",
            "B25034_008E": "100",
            "B25034_009E": "100",
            "B25034_010E": "50",
            "B25034_011E": "250",
            "B25040_001E": "1000",
            "B25040_003E": "80",
            "B25040_004E": "300",
            "B25040_005E": "220",
            "B08303_001E": "1000",
            "B08303_011E": "60",
            "B08303_012E": "30",
            "B08303_013E": "10",
        }
    )
    m = census.metrics_from_values(census.parse_response(body))

    assert m.population == 50_000
    assert m.median_household_income == 61_000
    assert m.poverty_rate == pytest.approx(0.15)
    assert m.pre1980_housing_share == pytest.approx(0.60)
    assert m.delivered_fuel_heat_share == pytest.approx(0.30)  # propane + fuel oil
    assert m.electric_heat_share == pytest.approx(0.30)
    assert m.long_commute_share == pytest.approx(0.10)


def test_census_sentinels_become_none():
    body = census_body({"B19013_001E": "-666666666", "B25040_003E": "-999999999"})
    m = census.metrics_from_values(census.parse_response(body))
    assert m.median_household_income is None
    assert m.delivered_fuel_heat_share is None  # a missing part voids the share
    assert m.renter_share == pytest.approx(1.0)


def test_census_without_key_fails_before_any_request(settings):
    def handler(request):
        raise AssertionError("no request should be made")

    async def run():
        async with client_returning(handler) as client:
            await census.fetch_county(client, settings, "36", "007")

    with pytest.raises(SourceError, match="CENSUS_API_KEY"):
        asyncio.run(run())


def test_census_fetch_caches_and_reports_bad_key(settings):
    keyed = settings.model_copy(update={"census_api_key": "k"})
    calls = []

    def handler(request):
        calls.append(request.url)
        assert request.url.params["for"] == "county:007"
        assert request.url.params["in"] == "state:36"
        return httpx.Response(200, json=census_body())

    async def run():
        async with client_returning(handler) as client:
            first = await census.fetch_county(client, keyed, "36", "007")
            second = await census.fetch_county(client, keyed, "36", "007")
            return first, second

    (m1, cached1), (m2, cached2) = asyncio.run(run())
    assert (cached1, cached2) == (False, True)
    assert m1 == m2
    assert len(calls) == 1

    async def rejected():
        async with client_returning(lambda r: httpx.Response(302)) as client:
            await census.fetch_national(client, keyed)

    with pytest.raises(SourceError, match="rejected the API key"):
        asyncio.run(rejected())


# --- pvwatts ------------------------------------------------------------------


def test_pvwatts_parses_yield_and_rounds_cache_key(settings):
    keyed = settings.model_copy(update={"nlr_api_key": "k"})
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json={"errors": [], "outputs": {"ac_annual": 1170.65}})

    async def run():
        async with client_returning(handler) as client:
            a = await pvwatts.fetch_yield(client, keyed, 42.1012, -75.9049)
            b = await pvwatts.fetch_yield(client, keyed, 42.0991, -75.9012)  # same cell
            return a, b

    (value, cached_a), (_, cached_b) = asyncio.run(run())
    assert value == pytest.approx(1170.65)
    assert (cached_a, cached_b) == (False, True)
    assert len(calls) == 1
    assert calls[0]["system_capacity"] == "1"
    assert calls[0]["lat"] == "42.1"


def test_pvwatts_errors_are_not_cached(settings):
    keyed = settings.model_copy(update={"nlr_api_key": "k"})
    responses = [
        httpx.Response(200, json={"errors": ["bad location"], "outputs": {}}),
        httpx.Response(200, json={"errors": [], "outputs": {"ac_annual": 1000}}),
    ]

    async def run():
        async with client_returning(lambda r: responses.pop(0)) as client:
            with pytest.raises(SourceError, match="bad location"):
                await pvwatts.fetch_yield(client, keyed, 10.0, 10.0)
            return await pvwatts.fetch_yield(client, keyed, 10.0, 10.0)

    assert asyncio.run(run()) == (1000.0, False)


# --- stations -------------------------------------------------------------------


def test_stations_are_trimmed(settings):
    keyed = settings.model_copy(update={"nlr_api_key": "k"})
    body = {
        "total_results": 2,
        "fuel_stations": [
            {
                "id": 1,
                "station_name": "Town Hall",
                "city": "Springfield",
                "latitude": 44.1,
                "longitude": -72.5,
                "ev_level2_evse_num": 2,
                "ev_dc_fast_num": None,
                "lots_of": "other fields",
            },
            {"id": 2, "station_name": "No location", "latitude": None, "longitude": None},
        ],
    }

    def handler(request):
        assert request.url.params["fuel_type"] == "ELEC"
        assert request.url.params["access"] == "public"
        return httpx.Response(200, json=body)

    async def run():
        async with client_returning(handler) as client:
            stations, _ = await afdc.fetch_state_stations(client, keyed, "VT")
            count, _ = await afdc.fetch_national_count(client, keyed)
            return stations, count

    stations, count = asyncio.run(run())
    assert stations == [
        {
            "id": 1,
            "name": "Town Hall",
            "city": "Springfield",
            "lat": 44.1,
            "lon": -72.5,
            "level2_ports": 2,
            "dc_fast_ports": None,
        }
    ]
    assert count == 2


# --- overpass -------------------------------------------------------------------


def test_overpass_query_and_parsing():
    query = overpass.build_query(42.0, -76.2, 42.4, -75.4, 15)
    assert "[out:json][timeout:15]" in query
    assert "(42.0,-76.2,42.4,-75.4)" in query  # south, west, north, east

    elements = [
        {
            "type": "node",
            "id": 1,
            "lat": 42.1,
            "lon": -75.9,
            "tags": {"amenity": "library", "name": "Main Library"},
        },
        {
            "type": "way",
            "id": 2,
            "center": {"lat": 42.2, "lon": -75.8},
            "tags": {"amenity": "university", "name": "State U"},
        },
        {
            "type": "node",
            "id": 3,
            "lat": 42.1,
            "lon": -75.9,
            "tags": {"amenity": "school"},
        },  # unnamed
        {"type": "way", "id": 4, "tags": {"amenity": "school", "name": "No center"}},
        {
            "type": "node",
            "id": 5,
            "lat": 42.1,
            "lon": -75.9,
            "tags": {"amenity": "cafe", "name": "Cafe"},
        },
    ]
    parsed = overpass.parse_elements(elements)
    assert [(b["osm_id"], b["type"], b["name"]) for b in parsed] == [
        ("node/1", "library", "Main Library"),
        ("way/2", "college", "State U"),
    ]


def test_overpass_sends_user_agent_and_reports_busy(settings):
    seen = {}

    def ok(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json={"elements": []})

    async def run(handler):
        async with client_returning(handler) as client:
            return await overpass.fetch_public_buildings(
                client, settings, "36007", (-76.2, 42.0, -75.4, 42.4)
            )

    assert asyncio.run(run(ok)) == ([], False)
    assert "county-sustainability-planner" in seen["ua"]

    cache.clear_memory()
    settings.cache_dir.joinpath("x").parent.mkdir(parents=True, exist_ok=True)
    for path in settings.cache_dir.glob("overpass_*"):
        path.unlink()
    with pytest.raises(SourceError, match="busy"):
        asyncio.run(run(lambda r: httpx.Response(429)))


# --- state energy file -----------------------------------------------------------


def test_state_energy_lookup(tmp_path):
    from app.sources import state_energy

    with pytest.raises(SourceError, match="prep_data.py energy"):
        state_energy.for_state(tmp_path, "WV")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "state_energy.json").write_text(
        '{"electricity_year": 2024, "grid_year": 2023, "states": {'
        '"WV": {"residential_monthly_bill_usd": 154.76, "grid_co2_lb_per_mwh": 1954.4},'
        '"US": {"residential_monthly_bill_usd": 142.26, "grid_co2_lb_per_mwh": 767.2}}}'
    )
    found = state_energy.for_state(data_dir, "WV")
    assert found["state"]["grid_co2_lb_per_mwh"] == 1954.4
    assert found["national"]["residential_monthly_bill_usd"] == 142.26
    assert (found["electricity_year"], found["grid_year"]) == (2024, 2023)
    assert state_energy.for_state(data_dir, "ZZ") is None
