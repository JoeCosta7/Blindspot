"""The sources added 2026-11-14: parsing, need rules, factors and the energy
community funding check. No real network calls and no prepared files."""

import asyncio
import json

import httpx
import pytest
from shapely.geometry import box

from app import factors, funding, needs, pipeline, profile
from app.config import BACKEND_DIR
from app.sources import (
    SourceError,
    _arcgis,
    attains,
    drinking_water,
    openfema,
    transit,
    uswtdb,
)

PROGRAMS = funding.load_programs(BACKEND_DIR / "data" / "funding_programs.json")


def client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- need rules -----------------------------------------------------------------


def test_energy_burden_need_compares_low_income_households(settings):
    burden = {
        "county": {"burden_low_income": 0.12, "burden_all": 0.04},
        "national": {"burden_low_income": 0.08, "burden_all": 0.03},
    }
    need = needs.energy_burden_need(burden, settings)
    assert need is not None and need.id == "high_energy_burden"
    assert need.level == "high" and need.severity == 100  # ratio 1.5
    assert need.value == 0.12 and need.national_value == 0.08
    assert "12.0%" in need.detail and "4.0%" in need.detail
    burden["county"]["burden_low_income"] = 0.084  # ratio 1.05
    assert needs.energy_burden_need(burden, settings) is None
    assert needs.energy_burden_need({"county": {}, "national": None}, settings) is None


def test_utility_shutoff_need(settings):
    row = {"county": {"utility_shutoff_threat": 0.11}, "national": {"utility_shutoff_threat": 0.08}}
    need = needs.utility_shutoff_need(row, settings)
    assert need is not None and need.level == "high" and need.theme == "energy"
    row["county"]["utility_shutoff_threat"] = None  # state skipped the survey module
    assert needs.utility_shutoff_need(row, settings) is None


def system(name, population, health=False, priority=False):
    return {
        "name": name,
        "population": population,
        "health_violation": health,
        "enforcement_priority": priority,
    }


def test_drinking_water_need_levels(settings):
    clean = [system("Town", 5000), system("Park", 50)]
    assert needs.drinking_water_need(clean, settings) is None
    assert needs.drinking_water_need([], settings) is None

    some = [system("Town", 6000), system("PSD", 2000, health=True)]
    need = needs.drinking_water_need(some, settings)
    assert need is not None and need.level == "elevated" and need.theme == "water"
    assert need.value == pytest.approx(0.25)
    assert "1 of 2" in need.detail and "PSD (2,000 people)" in need.detail

    some.append(system("Hollow", 80, health=True, priority=True))
    need = needs.drinking_water_need(some, settings)
    assert need.level == "high" and "1 of them are EPA enforcement priorities" in need.detail


def test_impaired_waters_need(settings):
    waters = {
        "county": {"assessed": 100, "impaired": 80, "causes": {"bacteria": 40, "metals": 20}},
        "national": {"assessed": 1000, "impaired": 500},
    }
    need = needs.impaired_waters_need(waters, settings)
    assert need is not None and need.level == "high" and need.value == 0.8
    assert "bacteria, metals" in need.detail
    waters["county"]["assessed"] = settings.attains_min_assessed - 1
    assert needs.impaired_waters_need(waters, settings) is None


def test_air_quality_need_uses_the_worst_pollutant(settings):
    assert needs.air_quality_need({"pm25_annual_ugm3": 7.0, "ozone_ppm": 0.060}, settings) is None
    near = needs.air_quality_need({"pm25_annual_ugm3": 8.5, "ozone_ppm": 0.060}, settings)
    assert near is not None and near.level == "elevated" and near.unit == "ug_m3"
    over = needs.air_quality_need({"pm25_annual_ugm3": 8.5, "ozone_ppm": 0.080}, settings)
    assert over is not None and over.level == "high" and over.unit == "ppm"
    assert over.value == 0.080 and over.national_value == settings.ozone_standard_ppm
    assert over.theme == "health" and over.severity >= settings.severity_high
    assert over.detail.startswith("Ozone: 0.080 ppm against a standard of 0.070 ppm")


def test_new_themes_rank_in_the_top_tier(settings):
    air = needs.air_quality_need({"ozone_ppm": 0.080}, settings)
    ranked = needs.prioritize([air], settings)
    assert ranked[0].priority == ranked[0].severity


# --- funding: the energy community check ------------------------------------------


def bonus(matches):
    return next((m for m in matches if m.program_id == "energy_community_bonus"), None)


def test_energy_community_bonus_follows_the_treasury_data():
    def matched(status, asset_type="landfill", checked=True):
        return bonus(
            funding.match_funding(
                PROGRAMS,
                "community_solar",
                asset_type,
                energy_community=status,
                energy_community_checked=checked,
            )
        )

    whole = matched({"statistical_area_v2": True})
    assert whole is not None and "whole county" in whole.note
    part = matched({"coal_closure_tracts": 3, "total_tracts": 12})
    assert part is not None and "3 of 12 census tracts" in part.note
    assert matched({"coal_closure_tracts": 0}) is None
    assert matched(None) is None
    brownfield = matched(None, asset_type="brownfield")
    assert brownfield is not None and "brownfield" in brownfield.note
    unchecked = matched(None, checked=False)
    assert unchecked is not None and "could not be checked" in unchecked.note
    # Programs that do not depend on location carry no note.
    others = funding.match_funding(PROGRAMS, "community_solar", "landfill", {}, True)
    assert all(m.note is None for m in others)


def test_repowering_a_plant_gets_the_clean_electricity_credits():
    ids = {
        m.program_id
        for m in funding.match_funding(
            PROGRAMS, "repower_fossil_plant", "power_plant", {"statistical_area_v1": True}, True
        )
    }
    assert ids == {"elective_pay_48e_45y", "energy_community_bonus"}


# --- assets and factors ----------------------------------------------------------


def plant_row(plant_id, **overrides):
    row = {
        "plant_id": plant_id,
        "name": f"PLANT {plant_id}",
        "lat": 0.5,
        "lon": 0.5,
        "primary_technology": "Solar Photovoltaic",
        "operable_mw": 5.0,
        "fossil_mw": 0.0,
        "planned_retirement_mw": 0.0,
        "planned_retirement_year": None,
        "retired_fossil_mw": 0.0,
        "last_retirement_year": None,
        "reuse_candidate": False,
        "grid_voltage_kv": 34.5,
        "utility_name": "Utility",
        "sector": "IPP Non-CHP",
        "city": "Town",
    }
    return {**row, **overrides}


def test_plant_assets_put_repowering_candidates_first():
    rows = [
        plant_row(1, operable_mw=200.0),
        plant_row(
            2,
            primary_technology="Conventional Steam Coal",
            operable_mw=0.0,
            retired_fossil_mw=300.0,
            last_retirement_year=2019,
            reuse_candidate=True,
        ),
    ]
    assets = pipeline.plant_assets(rows)
    assert [a.id for a in assets] == ["eia860:2", "eia860:1"]
    coal = assets[0]
    assert coal.type == "power_plant" and coal.name == "Plant 2"
    assert coal.attributes["reuse_mw"] == 300.0 and coal.attributes["reuse_candidate"] is True
    assert assets[1].attributes["reuse_mw"] is None


def test_new_factors(settings):
    plants = pipeline.plant_assets(
        [plant_row(1), plant_row(2, primary_technology="Natural Gas Fired Combined Cycle",
                                 operable_mw=400.0, planned_retirement_mw=400.0,
                                 planned_retirement_year=2030, reuse_candidate=True)]
    )
    built = factors.build_factors(
        acs=None,
        acs_national=None,
        energy=None,
        nri_row=None,
        solar_yield=None,
        farm_row=None,
        ee_metrics=None,
        assets=plants,
        settings=settings,
        extra=factors.CountyExtras(
            burden={
                "county": {"burden_low_income": 0.1, "burden_all": 0.03},
                "national": {"burden_low_income": 0.08, "burden_all": 0.03},
            },
            utilities=[
                {"utility_name": "Rural Electric Coop", "ownership": "Cooperative",
                 "residential_price_cents_kwh": 13.2},
                {"utility_name": "Big Power Co", "ownership": "Investor Owned",
                 "residential_price_cents_kwh": None},
            ],
            energy_community={"coal_closure_tracts": 2, "total_tracts": 9},
            energy_community_checked=True,
            turbines={"turbines": 40, "projects": 2, "capacity_mw": 80.0, "latest_year": 2021},
            rucc={"rucc_code": 7, "rucc_description": "Nonmetro - small town"},
            disasters={"count": 3, "since_year": 2006, "by_type": {"Flood": 2, "Severe Storm": 1},
                       "latest_title": "SEVERE STORMS AND FLOODING", "latest_date": "2024-06-01"},
            wildfire={"risk_to_homes_national_rank": 0.84, "housing_directly_exposed": 0.5},
            air={"pm25_annual_ugm3": 7.1, "ozone_ppm": 0.062},
            water_systems=[system("Town", 1000, health=True), system("Park", 50)],
            waters={"county": {"assessed": 10, "impaired": 4, "causes": {"sediment": 3}},
                    "national": {"assessed": 100, "impaired": 50}},
            transit={"stops": 104, "agencies": 4},
        ),
    )
    by_id = {f.id: f for f in built}
    assert by_id["energy_burden"].value.startswith("10.0% for households below 200%")
    assert by_id["electric_utilities"].value == (
        "Rural Electric Coop (co-op, 13.2 cents per kWh for homes); Big Power Co (investor-owned)"
    )
    assert by_id["energy_community"].value.startswith("Part of the county: 2 of 9")
    assert by_id["wind_turbines"].value == "40 turbines in 2 projects, 80.0 MW"
    assert by_id["power_plants"].value.startswith("2 plants, 405 MW operating")
    assert "400 MW of fossil units with a planned retirement date" in by_id["power_plants"].detail
    assert by_id["rural_urban"].value == "Code 7 of 9: Nonmetro - small town"
    assert by_id["disaster_declarations"].value == "3 declarations: 2 flood, 1 severe storm"
    assert by_id["wildfire_risk_to_homes"].value == (
        "84th percentile among US counties; 50.0% of homes directly exposed"
    )
    assert by_id["air_quality"].value.startswith("PM2.5 7.1 ug/m3 (standard 9.0)")
    assert by_id["drinking_water_systems"].value == (
        "2 systems serving 1,050 people; 1 with a health-based violation"
    )
    assert by_id["assessed_waters"].comparison == "US: 50.0%"
    assert by_id["transit_stops"].value == "104 stops from 4 agencies"


def test_energy_community_factor_is_silent_when_unchecked(settings):
    b = factors._Builder()
    factors._energy_community_factor(b, None, checked=False)
    assert b.factors == []
    factors._energy_community_factor(b, None, checked=True)
    assert b.factors[0].value.startswith("No:")


def test_places_profile_has_national_figures():
    facts = profile.places_profile(
        {"county": {"asthma": 0.12, "copd": None}, "national": {"asthma": 0.10}}
    )
    assert [(f.id, f.value, f.national_value) for f in facts] == [("adult_asthma", 0.12, 0.10)]
    assert facts[0].category == "community" and "weighted by adult population" in facts[0].note


# --- API sources ------------------------------------------------------------------


def test_openfema_counts_each_disaster_once(settings):
    seen = {}
    rows = [
        {"disasterNumber": 1, "declarationDate": "2020-01-01T00:00:00.000Z",
         "incidentType": "Flood", "declarationTitle": "FLOODING"},
        {"disasterNumber": 1, "declarationDate": "2020-01-01T00:00:00.000Z",
         "incidentType": "Flood", "declarationTitle": "FLOODING"},
        {"disasterNumber": 2, "declarationDate": "2024-05-01T00:00:00.000Z",
         "incidentType": "Severe Storm", "declarationTitle": "SEVERE STORMS"},
    ]

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"DisasterDeclarationsSummaries": rows})

    async def go():
        async with client_returning(handler) as client:
            return await openfema.fetch_declarations(client, settings, "54", "055")

    summary, cached = asyncio.run(go())
    assert summary["count"] == 2 and summary["by_type"] == {"Flood": 1, "Severe Storm": 1}
    assert summary["latest_title"] == "SEVERE STORMS" and summary["latest_date"] == "2024-05-01"
    assert "fipsCountyCode eq '055'" in seen["$filter"]
    assert "declarationType eq 'DR'" in seen["$filter"]
    assert cached is False


def test_openfema_errors(settings):
    async def go():
        async with client_returning(lambda r: httpx.Response(503)) as client:
            return await openfema.fetch_declarations(client, settings, "54", "055")

    with pytest.raises(SourceError, match="HTTP 503"):
        asyncio.run(go())


def test_echo_runs_the_query_then_pages(settings):
    calls = []

    def handler(request):
        calls.append(request.url.path.rsplit(".", 1)[-1])
        if request.url.path.endswith("get_systems"):
            assert request.url.params["p_fips"] == "54055"
            assert request.url.params["p_pswt"] == "CWS" and request.url.params["p_act"] == "Y"
            return httpx.Response(200, json={"Results": {"QueryRows": "2", "QueryID": "9"}})
        row = {"PWSId": "WV1", "PWSName": "BLUEWELL PSD", "PopulationServedCount": "6216",
               "HealthFlag": "Yes", "SeriousViolator": "Yes", "QtrsWithVio": "12", "PbAle": None}
        other = {**row, "PWSId": "WV2", "HealthFlag": "No", "SeriousViolator": "No"}
        page = request.url.params["pageno"]
        return httpx.Response(
            200, json={"Results": {"WaterSystems": [row] if page == "1" else [other]}}
        )

    drinking_water.PAGE_SIZE, saved = 1, drinking_water.PAGE_SIZE
    try:

        async def go():
            async with client_returning(handler) as client:
                return await drinking_water.fetch_water_systems(client, settings, "54055")

        systems, _ = asyncio.run(go())
    finally:
        drinking_water.PAGE_SIZE = saved
    assert calls == ["get_systems", "get_qid", "get_qid"]
    assert systems[0] == {
        "id": "WV1",
        "name": "BLUEWELL PSD",
        "population": 6216,
        "health_violation": True,
        "enforcement_priority": True,
        "quarters_with_violation": 12,
        "lead_exceedances": 0,
        "contaminants_3yr": None,
        "source_water": None,
        "owner": None,
    }
    assert systems[1]["health_violation"] is False


def test_echo_with_no_systems_makes_one_call(settings):
    def handler(request):
        return httpx.Response(200, json={"Results": {"QueryRows": "0", "QueryID": "1"}})

    async def go():
        async with client_returning(handler) as client:
            return await drinking_water.fetch_water_systems(client, settings, "48301")

    assert asyncio.run(go())[0] == []


def test_esri_polygon_is_clockwise_and_closed():
    rings = _arcgis.esri_polygon(box(0, 0, 1, 1))["rings"]
    assert len(rings) == 1 and rings[0][0] == rings[0][-1]
    # Shoelace: negative area means clockwise.
    ring = rings[0]
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False))
    assert area < 0


def test_attains_summarizes_status_and_causes(settings):
    def handler(request):
        form = dict(httpx.QueryParams(request.content.decode()))
        if "outStatistics" in form:
            features = [
                {"attributes": {"overallstatus": "Fully Supporting", "n": 3}},
                {"attributes": {"overallstatus": "Not Supporting", "n": 2}},
                {"attributes": {"overallstatus": "Not Assessed", "n": 5}},
            ]
            return httpx.Response(200, json={"features": features})
        assert form["where"] == "overallstatus='Not Supporting'"
        assert json.loads(form["geometry"])["rings"]
        return httpx.Response(
            200,
            json={"features": [{"attributes": {"assessmentunitname": "Elkhorn Creek",
                                               "pathogens": "Cause", "metals_other_than_mercury":
                                               "Meeting Criteria"}}]},
        )

    async def go():
        async with client_returning(handler) as client:
            return await attains.fetch_county_waters(client, settings, "54055", box(0, 0, 1, 1))

    result, _ = asyncio.run(go())
    county = result["county"]
    # Three layers, each answering the same counts.
    assert county["assessed"] == 15 and county["impaired"] == 6 and county["not_assessed"] == 15
    assert county["causes"] == {"bacteria and other pathogens": 3}
    assert county["impaired_names"] == ["Elkhorn Creek"]
    assert result["national"]["assessed"] == 15


def test_arcgis_service_errors_become_source_errors(settings):
    async def go():
        async with client_returning(
            lambda r: httpx.Response(200, json={"error": {"code": 400, "message": "Bad geometry"}})
        ) as client:
            return await transit.fetch_stops(client, settings, "54055", box(0, 0, 1, 1))

    with pytest.raises(SourceError, match="Bad geometry"):
        asyncio.run(go())


def test_transit_counts_stops_and_agencies(settings):
    def handler(request):
        features = [
            {"attributes": {"ntd_id": "30121", "n": 98}},
            {"attributes": {"ntd_id": "30164", "n": 4}},
            {"attributes": {"ntd_id": None, "n": 2}},
        ]
        return httpx.Response(200, json={"features": features})

    async def go():
        async with client_returning(handler) as client:
            return await transit.fetch_stops(client, settings, "54055", box(0, 0, 1, 1))

    assert asyncio.run(go())[0] == {"stops": 104, "agencies": 2}


def test_uswtdb_summary(settings):
    rows = [
        {"p_name": "Century", "p_year": 2007, "t_cap": 1500, "t_offshore": 0},
        {"p_name": "Century", "p_year": 2007, "t_cap": 1500, "t_offshore": 0},
        {"p_name": "Later", "p_year": 2017, "t_cap": None, "t_offshore": 0},
    ]
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=rows)

    async def go():
        async with client_returning(handler) as client:
            return await uswtdb.fetch_turbines(client, settings, "19197")

    summary, _ = asyncio.run(go())
    assert seen["t_fips"] == "eq.19197"
    assert summary == {
        "turbines": 3,
        "with_capacity": 2,
        "capacity_mw": 3.0,
        "projects": 2,
        "offshore": 0,
        "first_year": 2007,
        "latest_year": 2017,
    }
