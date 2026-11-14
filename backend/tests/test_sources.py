"""Source parsing and HTTP error handling, with no real network calls."""

import asyncio

import httpx
import pytest

from app import cache
from app.sources import SourceError, afdc, census, earthengine, geoapify, pvwatts
from tests.conftest import make_county


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


def test_census_profile_formulas():
    body = census_body(
        {
            "B01002_001E": "44.3",
            "B06009_001E": "1000",
            "B06009_002E": "120",
            "B06009_005E": "150",
            "B06009_006E": "50",
            "B28002_001E": "800",
            "B28002_004E": "600",
            "C24050_001E": "2000",
            "C24050_002E": "100",
            "C24050_003E": "160",
            "C24050_057E": "300",
            "C24050_071E": "400",
        }
    )
    m = census.metrics_from_values(census.parse_response(body))

    assert m.median_age == pytest.approx(44.3)
    assert m.less_than_high_school_share == pytest.approx(0.12)
    assert m.bachelors_or_higher_share == pytest.approx(0.20)  # bachelor's + graduate
    assert m.no_broadband_share == pytest.approx(0.25)  # 1 - broadband of any type
    assert m.trades_occupation_share == pytest.approx(0.15)
    assert m.production_transport_occupation_share == pytest.approx(0.20)
    assert m.agriculture_mining_industry_share == pytest.approx(0.05)
    assert m.construction_industry_share == pytest.approx(0.08)


def test_census_variables_fit_one_call():
    assert len(set(census.VARIABLES)) == len(census.VARIABLES)
    assert len(census.VARIABLES) <= 49  # 50 per call, NAME included


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


# --- geoapify -------------------------------------------------------------------


def feature(name, categories, lat=42.1, lon=-75.9, place_id="p1"):
    return {
        "type": "Feature",
        "properties": {
            "name": name,
            "categories": ["education", *categories],
            "lat": lat,
            "lon": lon,
            "city": "Town",
            "place_id": place_id,
        },
    }


def test_geoapify_parsing():
    features = [
        feature("Main Library", ["education.library", "building.college"], place_id="p1"),
        feature("State U", ["education.university"], place_id="p2"),
        feature("Old School", ["education.school"], place_id="p3"),
        feature("", ["education.school"], place_id="p4"),  # unnamed
        feature("Cafe", ["catering.cafe"], place_id="p5"),  # not a building type
        feature("No id", ["education.school"], place_id=None),  # cannot be linked
    ]
    parsed = geoapify.parse_features(features)
    assert [(b["place_id"], b["type"], b["name"]) for b in parsed] == [
        ("p1", "library", "Main Library"),
        ("p2", "college", "State U"),
        ("p3", "school", "Old School"),
    ]
    assert parsed[0]["city"] == "Town"


def fetch_buildings(settings, handler):
    async def run():
        async with client_returning(handler) as client:
            return await geoapify.fetch_public_buildings(
                client, settings, "36007", (-76.0, 42.0, -75.0, 43.0)
            )

    return asyncio.run(run())


def test_geoapify_needs_a_key(settings):
    with pytest.raises(SourceError, match="GEOAPIFY_API_KEY is not set"):
        fetch_buildings(settings, lambda r: httpx.Response(200, json={"features": []}))


def test_geoapify_request_and_errors(settings):
    keyed = settings.model_copy(update={"geoapify_api_key": "k"})
    seen = {}

    def ok(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"features": [feature("A School", ["education.school"])]})

    rows, cached = fetch_buildings(keyed, ok)
    assert [r["name"] for r in rows] == ["A School"] and cached is False
    assert seen["filter"] == "rect:-76.0,42.0,-75.0,43.0"  # west, south, east, north
    assert seen["limit"] == "500" and seen["apiKey"] == "k"
    assert "education.school" in seen["categories"]

    for status, message in [(401, "rejected"), (429, "quota"), (500, "HTTP 500")]:
        cache.clear_memory()
        for path in keyed.cache_dir.glob("geoapify_*"):
            path.unlink()
        with pytest.raises(SourceError, match=message):
            fetch_buildings(keyed, lambda r, s=status: httpx.Response(s))


def test_geoapify_splits_full_boxes_and_dedupes(settings, monkeypatch):
    """A box that comes back full (the API's 500 cap) is split into quarters.
    Here the cap is 3 and only the whole county's box is full."""
    monkeypatch.setattr(geoapify, "PAGE_LIMIT", 3)
    keyed = settings.model_copy(update={"geoapify_api_key": "k"})
    boxes = []

    def handler(request):
        box = request.url.params["filter"]
        boxes.append(box)
        if box == "rect:-76.0,42.0,-75.0,43.0":
            names = ["A", "B", "C"]  # full: triggers the split
            return httpx.Response(
                200,
                json={
                    "features": [
                        feature(n, ["education.school"], place_id=f"w{i}")
                        for i, n in enumerate(names)
                    ]
                },
            )
        # Every quarter returns place w1 (an edge duplicate) plus one of its own.
        own = len(boxes)
        return httpx.Response(
            200,
            json={
                "features": [
                    feature("B", ["education.school"], place_id="w1"),
                    feature(f"Q{own}", ["education.school"], place_id=f"w{100 + own}"),
                ]
            },
        )

    rows, _ = fetch_buildings(keyed, handler)
    assert len(boxes) == 5  # the county box, then its four quarters
    assert sorted(r["name"] for r in rows) == ["B", "Q2", "Q3", "Q4", "Q5"]


def test_split_box():
    quarters = geoapify.split_box((0.0, 0.0, 2.0, 2.0))
    assert quarters == [
        (0.0, 0.0, 1.0, 1.0),
        (1.0, 0.0, 2.0, 1.0),
        (0.0, 1.0, 1.0, 2.0),
        (1.0, 1.0, 2.0, 2.0),
    ]


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


# --- earth engine -------------------------------------------------------------


def test_earth_engine_needs_a_project(settings):
    county = make_county("99001", 0, 0, 1, 1)
    with pytest.raises(SourceError, match="EE_PROJECT"):
        asyncio.run(earthengine.fetch_county_metrics(settings, county))


def test_earth_engine_result_is_cached_by_county(settings, monkeypatch):
    calls = []

    def fake_compute(project, county):
        calls.append(county.fips)
        return {"heat_island_c": 7.0, "canopy_built_up": 0.1}

    monkeypatch.setattr(earthengine, "_compute", fake_compute)
    keyed = settings.model_copy(update={"ee_project": "demo-project"})
    county = make_county("99001", 0, 0, 1, 1)

    async def run():
        return [await earthengine.fetch_county_metrics(keyed, county) for _ in range(2)]

    (first, first_cached), (second, second_cached) = asyncio.run(run())
    assert first == second and (first_cached, second_cached) == (False, True)
    assert calls == ["99001"]
