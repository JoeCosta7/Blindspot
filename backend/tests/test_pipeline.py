"""The whole pipeline with every source faked. No network, no prepared files."""

import asyncio

import pytest

from app import factors, narrative, opportunities, pipeline
from app.sources import (
    SourceError,
    afdc,
    census,
    dams,
    earthengine,
    farms,
    geoapify,
    lmop,
    nri,
    pvwatts,
    repowering,
    state_energy,
)
from tests.conftest import make_county, make_metrics

COUNTY = make_county("99001", 0, 0, 1, 1)


def returning(value, cached=False):
    async def fake(*args, **kwargs):
        return value, cached

    return fake


def raising(exc):
    async def fake(*args, **kwargs):
        raise exc

    return fake


@pytest.fixture
def sources(monkeypatch):
    """Healthy fakes for every source. Tests override individual ones."""
    county = make_metrics(
        population=20_000,
        median_household_income=55_000.0,  # ratio 1.45 -> high
        pre1980_housing_share=0.70,  # ratio 1.4 -> high
    )
    monkeypatch.setattr(census, "fetch_county", returning(county))
    monkeypatch.setattr(census, "fetch_national", returning(make_metrics(population=330_000_000)))
    monkeypatch.setattr(pvwatts, "fetch_yield", returning(1200.0))
    monkeypatch.setattr(afdc, "fetch_national_count", returning(82_500))
    monkeypatch.setattr(
        afdc,
        "fetch_state_stations",
        returning(
            [
                {
                    "id": 1,
                    "name": "Inside",
                    "city": None,
                    "lat": 0.5,
                    "lon": 0.5,
                    "level2_ports": 2,
                    "dc_fast_ports": None,
                },
                {
                    "id": 2,
                    "name": "Other county",
                    "city": None,
                    "lat": 5.0,
                    "lon": 5.0,
                    "level2_ports": 2,
                    "dc_fast_ports": None,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        geoapify,
        "fetch_public_buildings",
        returning(
            [
                {
                    "osm_id": "node/1",
                    "type": "library",
                    "name": "Main Library",
                    "lat": 0.5,
                    "lon": 0.5,
                    "city": None,
                },
                {
                    "osm_id": "way/2",
                    "type": "school",
                    "name": "Central School",
                    "lat": 0.6,
                    "lon": 0.6,
                    "city": None,
                },
                {
                    "osm_id": "way/3",
                    "type": "school",
                    "name": "Bbox Only School",
                    "lat": 1.5,
                    "lon": 0.5,
                    "city": None,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        repowering,
        "sites_for_county",
        lambda data_dir, fips: [
            {
                "site_id": 10,
                "name": "OLD MILL SITE",
                "site_type": "brownfield",
                "program": "BROWNFIELDS",
                "city": "MILLTOWN",
                "lat": 0.4,
                "lon": 0.4,
                "acres": 69.0,
                "pv_scale": "utility",
                "wind_scale": None,
                "epa_pv_mw": 10.0,
                "transmission_miles": 1.23,
                "profile_url": None,
            },
            {
                "site_id": 11,
                "name": "Depot",
                "site_type": "contaminated_site",
                "program": "RCRA",
                "city": None,
                "lat": 0.3,
                "lon": 0.3,
                "acres": None,
                "pv_scale": "unknown_acreage",
                "wind_scale": None,
                "epa_pv_mw": None,
                "transmission_miles": None,
                "profile_url": None,
            },
        ],
    )
    monkeypatch.setattr(
        lmop,
        "landfills_for_county",
        lambda data_dir, fips: [
            {
                "landfill_id": 7,
                "name": "County Landfill",
                "lat": 0.2,
                "lon": 0.2,
                "status": "Open",
                "project_status": "Candidate",
                "is_candidate": True,
                "has_operational_project": False,
                "operational_mw": None,
                "waste_in_place_tons": 2_000_000.0,
                "lfg_collected_mmscfd": 0.5,
                "lfg_collected_year": 2022.0,
                "lfg_generated_mmscfd": None,
                "ownership_type": "Public",
            },
            {
                "landfill_id": 8,
                "name": "No Coordinates LF",
                "lat": None,
                "lon": None,
                "status": "Closed",
                "project_status": "Low Potential",
                "is_candidate": False,
                "has_operational_project": False,
            },
        ],
    )
    monkeypatch.setattr(
        nri,
        "ratings_for_county",
        lambda data_dir, fips: {
            "nri_version": "December 2025",
            "wildfire_rating": "Relatively High",
        },
    )
    # The stretch sources find nothing unless a test says otherwise.
    monkeypatch.setattr(state_energy, "for_state", lambda data_dir, state: None)
    monkeypatch.setattr(dams, "dams_for_county", lambda data_dir, fips: [])
    monkeypatch.setattr(farms, "farms_for_county", lambda data_dir, fips: None)
    monkeypatch.setattr(earthengine, "fetch_county_metrics", returning(None))


def analyze(settings):
    return asyncio.run(pipeline.analyze_county(COUNTY, client=None, settings=settings))


def test_full_report(sources, settings):
    report = analyze(settings)

    assert (report.fips, report.population) == ("99001", 20_000)
    assert report.context.median_household_income == 55_000.0
    assert report.resources.solar_kwh_per_kw_year == 1200

    need_ids = [n.id for n in report.needs]
    assert set(need_ids) == {
        "low_income",
        "old_housing",
        "ev_charging_gap",  # 0.5 per 10k against a benchmark of 2.5
        "landfill_gas_uncaptured",
        "hazard_risk",
    }
    # Ordered by priority, not severity. Hazard risk and landfill gas count in
    # full and lead; income and housing count 0.7; the charger gap is the most
    # severe need here, but transportation counts half, so it comes last.
    assert [(n.id, n.severity, n.priority) for n in report.needs] == [
        ("hazard_risk", 75, 75),
        ("landfill_gas_uncaptured", 75, 75),
        ("low_income", 91, 64),
        ("old_housing", 80, 56),
        ("ev_charging_gap", 100, 50),
    ]
    assert report.opportunities[0].solution_id == "landfill_gas_energy"
    # The same holds for opportunities: chargers at the library rank below every
    # solution that answers an energy, waste, equity or resilience need.
    charging = [o for o in report.opportunities if o.solution_id == "public_ev_charging"]
    assert charging and all(o.score == 50 for o in charging)
    assert report.opportunities[-1].solution_id == "public_ev_charging"
    assert all(o.score > 50 for o in report.opportunities if o.solution_id != "public_ev_charging")

    # Points outside the county polygon are dropped.
    names = {a.name for a in report.assets}
    assert "Other county" not in names and "Bbox Only School" not in names
    assert "Old Mill Site" in names  # upper-case EPA names are tidied
    assert report.asset_counts == {
        "brownfield": 1,
        "contaminated_site": 1,
        "lmop_landfill": 1,
        "library": 1,
        "school": 1,
        "ev_station": 1,
    }
    assert set(report.missing_asset_types) == {
        "landfill",
        "mine_land",
        "college",
        "non_powered_dam",
    }

    by_id = {o.id: o for o in report.opportunities}
    assert len(report.opportunities) <= settings.max_opportunities
    solar = by_id["community_solar:repowering:10"]
    assert solar.need_ids == ["low_income"]
    assert {e.label: e.value for e in solar.estimates} == {
        "Capacity": 6.0,
        "Annual energy": 7200.0,
        "Household equivalent": 670.0,
    }
    assert all(e.is_estimate and e.assumptions for o in report.opportunities for e in o.estimates)
    assert solar.funding[0].program_id == "epa_brownfields_fy27"

    gas = by_id["landfill_gas_energy:lmop:7"]
    assert gas.funding == []
    assert gas.estimates[0].label == "Landfill gas collected"

    asset_ids = {a.id for a in report.assets}
    assert all(set(o.asset_ids) <= asset_ids for o in report.opportunities)

    # With no Gemini key: the rule matches and the template brief.
    assert {o.generated_by for o in report.opportunities} == {"rules"}
    assert all(o.summary is None and o.factor_ids == [] for o in report.opportunities)
    assert report.narrative.generated_by == "template"
    status = {s.source: s for s in report.source_status}
    assert all(s.ok for name, s in status.items() if name != "gemini")
    assert "GEMINI_API_KEY" in status["gemini"].message
    assert "no coordinates" in status["lmop"].message


def test_factors_describe_what_the_county_has(sources, settings):
    report = analyze(settings)
    by_id = {f.id: f for f in report.factors}

    solar = by_id["solar_resource"]
    assert (solar.category, solar.value) == ("climate", "1,200 kWh a year per kW of panels")
    assert by_id["natural_hazards"].value == "Wildfire: Relatively High"
    # Local talent, with the US figure beside it.
    jobless = by_id["unemployment"]
    assert (jobless.category, jobless.value, jobless.comparison) == (
        "workforce",
        "5.0%",
        "US: 5.0%",
    )
    assert by_id["construction_workforce"].value == "9.0%"
    # The housing stock and the sites, counted from what the sources returned.
    assert by_id["housing_pre1980"].value == "70.0%"
    assert by_id["sites_brownfield"].value == ("1 site; 69 acres across the 1 with a recorded size")
    assert by_id["sites_contaminated_site"].value == "1 site"  # no acreage recorded
    assert by_id["public_buildings"].value == "1 school, 1 library"
    assert by_id["ev_stations"].value == "1 station"
    assert "1 that EPA lists as a candidate" in by_id["landfill_gas_sites"].detail
    # Grouped by category, in a fixed order.
    categories = [f.category for f in report.factors]
    assert categories == sorted(categories, key=factors.CATEGORY_ORDER.index)
    # Nothing is listed for a source that returned nothing.
    assert not {"electricity_price", "farmland", "tree_canopy", "non_powered_dams"} & set(by_id)


def test_failed_sources_do_not_fail_the_request(sources, settings, monkeypatch):
    monkeypatch.setattr(census, "fetch_county", raising(SourceError("CENSUS_API_KEY is not set")))
    monkeypatch.setattr(geoapify, "fetch_public_buildings", raising(RuntimeError("boom")))

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(pvwatts, "fetch_yield", slow)
    quick = settings.model_copy(update={"source_timeout_s": 0.05})

    report = analyze(quick)
    status = {s.source: s for s in report.source_status}

    assert status["census"].ok is False and "CENSUS_API_KEY" in status["census"].message
    assert status["geoapify"].message == "Unexpected error: RuntimeError"
    assert "Timed out" in status["pvwatts"].message
    assert status["repowering"].ok and status["lmop"].ok and status["nri"].ok

    # Needs that do not depend on the failed sources are still found.
    assert {n.id for n in report.needs} == {"landfill_gas_uncaptured", "hazard_risk"}
    assert report.population is None
    assert report.resources.solar_kwh_per_kw_year is None
    # A failed source is "unknown", not "not found".
    assert "school" not in report.missing_asset_types
    assert [o.solution_id for o in report.opportunities] == ["landfill_gas_energy"]
    assert any("could not be loaded" in c for c in report.narrative.caveats)


def test_site_yield_failure_falls_back_to_county_yield(sources, settings, monkeypatch):
    calls = []

    async def centroid_only(client, s, lat, lon):
        calls.append((lat, lon))
        if (lat, lon) != (COUNTY.centroid_lat, COUNTY.centroid_lon):
            raise SourceError("PVWatts rate limit reached")
        return 1000.0, False

    monkeypatch.setattr(pvwatts, "fetch_yield", centroid_only)
    report = analyze(settings)

    solar = next(o for o in report.opportunities if o.solution_id == "community_solar")
    energy = next(e for e in solar.estimates if e.label == "Annual energy")
    assert energy.value == 6000.0
    assert any("county center, not the site" in a for a in energy.assumptions)
    assert len(calls) <= 1 + settings.max_pvwatts_sites


def test_map_caps_keep_opportunity_assets(sources, settings, monkeypatch):
    rows = [
        {
            "site_id": i,
            "name": f"Site {i}",
            "site_type": "brownfield",
            "program": "BROWNFIELDS",
            "lat": 0.5,
            "lon": 0.5,
            "acres": float(i),
            "pv_scale": None,
        }
        for i in range(1, 11)
    ]
    # The only screened site is the smallest, so the cap alone would drop it.
    rows[0]["pv_scale"] = "utility"
    monkeypatch.setattr(repowering, "sites_for_county", lambda data_dir, fips: rows)
    capped = settings.model_copy(update={"max_sites_per_county": 3})

    report = analyze(capped)
    shown = [a.id for a in report.assets if a.type == "brownfield"]

    assert report.asset_counts["brownfield"] == 10
    assert shown[:3] == ["repowering:10", "repowering:9", "repowering:8"]  # largest first
    assert "repowering:1" in shown and len(shown) == 4


def test_gemini_writes_the_opportunities_and_code_adds_the_numbers(sources, settings, monkeypatch):
    rows = [
        {
            "site_id": i,
            "name": f"Site {i}",
            "site_type": "mine_land",
            "program": "AML",
            "lat": 0.5,
            "lon": 0.5,
            "acres": float(i),
            "pv_scale": None,
        }
        for i in range(1, 11)
    ]
    monkeypatch.setattr(repowering, "sites_for_county", lambda data_dir, fips: rows)
    prompts = []

    async def fake_gemini(prompt, s):
        prompts.append(prompt)
        return opportunities.ModelPlan(
            summary="The county has 10 abandoned mine lands and builders to put to work.",
            opportunities=[
                {  # a catalogued project type at a site it can be built at
                    "title": "Mine land solar",
                    "need_ids": ["low_income"],
                    "factor_ids": ["solar_resource", "sites_mine_land"],
                    "site_ids": ["repowering:8"],
                    "project_type": "community_solar",
                    "what": "Offer Site 8 to a solar developer.",
                    "why_here": "The county has 10 abandoned mine lands.",
                    "first_steps": ["Check who owns the land."],
                },
                {  # an idea of Gemini's own: two problems that solve each other
                    "title": "Home repair crews",
                    "need_ids": ["old_housing", "low_income"],
                    "factor_ids": ["unemployment", "construction_workforce", "housing_pre1980"],
                    "site_ids": [],
                    "project_type": "other",
                    "what": "Train residents who are out of work to repair older homes.",
                    "why_here": "70.0% of homes were built before 1980.",
                    "first_steps": ["Meet local builders."],
                },
                {  # claims a project type, but at a site that type cannot use
                    "title": "Gas study",
                    "need_ids": ["landfill_gas_uncaptured"],
                    "factor_ids": [],
                    "site_ids": ["repowering:9"],
                    "project_type": "landfill_gas_energy",
                    "what": "Study gas capture with Site 9 as a staging area.",
                    "why_here": "The landfill has no gas project.",
                    "first_steps": ["Ask the operator."],
                },
                {
                    "title": "Invented",
                    "need_ids": ["low_income"],
                    "factor_ids": [],
                    "site_ids": ["repowering:1"],  # in the county, not in the digest
                    "project_type": "other",
                    "what": "x",
                    "why_here": "y",
                    "first_steps": ["z"],
                },
            ],
        )

    monkeypatch.setattr(opportunities, "_call_gemini", fake_gemini)
    # The map shows only the largest mine land, so the named ones must be kept.
    keyed = settings.model_copy(update={"gemini_api_key": "k", "max_sites_per_county": 1})

    report = analyze(keyed)

    # Ranked by code, from the priority of the needs each one addresses.
    assert [(o.id, o.title, o.score) for o in report.opportunities] == [
        ("ai:3", "Gas study", 75),
        ("ai:1", "Mine land solar", 64),
        ("ai:2", "Home repair crews", 62),  # halfway between 64 and the mean of 64 and 56
    ]
    assert {o.generated_by for o in report.opportunities} == {"gemini"}
    gas, solar, crews = report.opportunities

    # A typed opportunity gets estimates and funding leads from code.
    assert solar.solution_id == "community_solar" and solar.asset_ids == ["repowering:8"]
    assert solar.summary == "Offer Site 8 to a solar developer."
    assert solar.factor_ids == ["solar_resource", "sites_mine_land"]
    assert solar.first_steps == ["Check who owns the land."]
    assert [e.label for e in solar.estimates] == [
        "Capacity",
        "Annual energy",
        "Household equivalent",
    ]
    assert solar.funding and all(e.is_estimate for e in solar.estimates)

    # An idea of Gemini's own gets neither: there is no project type to size.
    assert crews.solution_id is None and crews.asset_ids == []
    assert crews.need_ids == ["low_income", "old_housing"]  # in priority order
    assert crews.factor_ids == ["unemployment", "construction_workforce", "housing_pre1980"]
    assert crews.estimates == [] and crews.funding == []
    # A project type claimed at a site that cannot host it is not trusted.
    assert gas.solution_id is None and gas.asset_ids == ["repowering:9"]
    assert gas.estimates == [] and gas.funding == []

    shown = {a.id for a in report.assets}
    assert {"repowering:10", "repowering:9", "repowering:8"} <= shown
    assert "repowering:7" not in shown
    status = {s.source: s for s in report.source_status}
    assert status["gemini"].ok and "1 of 4" in status["gemini"].message

    # The brief: Gemini's summary, caveats by code.
    assert report.narrative.generated_by == "gemini"
    assert report.narrative.summary.startswith("The county has 10 abandoned mine lands")
    assert report.narrative.caveats[0] == narrative.AI_CAVEAT

    # One call. The model was sent the needs, the factors and a digest of the
    # sites: three mine lands and a count, not all ten.
    assert len(prompts) == 1
    assert '"count": 10' in prompts[0]
    assert "repowering:8" in prompts[0] and "repowering:7" not in prompts[0]
    assert '"factor_id": "construction_workforce"' in prompts[0]
    assert '"need_id": "old_housing"' in prompts[0]


def test_rule_matches_are_served_when_gemini_fails(sources, settings, monkeypatch):
    monkeypatch.setattr(opportunities, "_call_gemini", raising(RuntimeError("quota")))
    report = analyze(settings.model_copy(update={"gemini_api_key": "k"}))

    assert report.opportunities and {o.generated_by for o in report.opportunities} == {"rules"}
    assert report.opportunities[0].solution_id == "landfill_gas_energy"
    assert report.narrative.generated_by == "template"
    status = next(s for s in report.source_status if s.source == "gemini")
    assert not status.ok and "RuntimeError" in status.message
    assert report.factors  # the factors do not depend on Gemini


def test_stretch_sources_add_needs_assets_and_opportunities(sources, settings, monkeypatch):
    monkeypatch.setattr(
        state_energy,
        "for_state",
        lambda data_dir, state: {
            "state_abbr": state,
            "state": {
                "residential_monthly_bill_usd": 180.0,
                "residential_price_cents_kwh": 15.07,
                "grid_co2_lb_per_mwh": 1900.0,
            },
            "national": {
                "residential_monthly_bill_usd": 144.0,
                "residential_price_cents_kwh": 16.48,
                "grid_co2_lb_per_mwh": 760.0,
            },
            "electricity_year": 2024,
            "grid_year": 2023,
        },
    )
    monkeypatch.setattr(
        dams,
        "dams_for_county",
        lambda data_dir, fips: [
            {
                "dam_id": "ZZ00001",
                "name": "MILL POND DAM",
                "lat": 0.7,
                "lon": 0.7,
                "capacity_mw": 2.5,
                "generation_mwh_yr": 9000.0,
                "capacity_factor": 0.41,
                "head_ft": 30.0,
                "waterway": "MILL RIVER",
                "owner_type": "Local Government",
                "city": None,
                "input_data": "Modeled Flow, Estimated Head",
            }
        ],
    )
    monkeypatch.setattr(
        farms,
        "farms_for_county",
        lambda data_dir, fips: {
            "land_in_farms_acres": 120_000,
            "cropland_acres": 80_000,
            "dairy_farms_500_plus": 3,
            "hog_farms_1000_plus": 0,
            "digesters_operational": 0,
            "digesters_under_construction": 0,
        },
    )
    # Room for every solution, so the assertions do not depend on ranking.
    report = analyze(settings.model_copy(update={"max_opportunities": 20}))

    needs_by_id = {n.id: n for n in report.needs}
    bill = needs_by_id["high_electricity_cost"]
    assert (bill.value, bill.national_value, bill.level, bill.severity) == (
        180.0,
        144.0,
        "high",
        50,
    )
    assert needs_by_id["carbon_intensive_grid"].severity == 100
    assert needs_by_id["unmanaged_farm_waste"].level == "high"

    assert report.asset_counts["non_powered_dam"] == 1
    assert "non_powered_dam" not in report.missing_asset_types
    assert report.context.state_electricity_price_cents_kwh == 15.07
    assert report.resources.land_in_farms_acres == 120_000

    by_solution = {o.solution_id: o for o in report.opportunities}
    hydro = by_solution["small_hydro"]
    assert hydro.title == "Hydropower at Mill Pond Dam"
    assert hydro.asset_ids == ["dam:ZZ00001"]
    assert [e.label for e in hydro.estimates] == [
        "Capacity",
        "Annual energy",
        "Household equivalent",
    ]
    assert [f.program_id for f in hydro.funding] == ["elective_pay_48e_45y_hydro"]

    digester = by_solution["farm_digester"]
    assert digester.asset_ids == [] and digester.estimates == []
    assert [(f.program_id, f.status) for f in digester.funding] == [("usda_reap", "uncertain")]

    # Opportunities that name a site still point at an asset on the map.
    shown = {a.id for a in report.assets}
    assert all(set(o.asset_ids) <= shown for o in report.opportunities)
    assert any("state averages" in c for c in report.narrative.caveats)

    # The same sources feed the factors.
    by_factor = {f.id: f for f in report.factors}
    price = by_factor["electricity_price"]
    assert (price.value, price.comparison) == ("15.1 cents per kWh", "US: 16.5 cents per kWh")
    assert by_factor["grid_carbon"].value == "1,900 pounds of CO2 per MWh"
    assert by_factor["farmland"].value == "120,000 acres in farms; 80,000 acres of cropland"
    assert by_factor["large_livestock_farms"].value == "3 dairy farms with 500 or more cows"
    dams_factor = by_factor["non_powered_dams"]
    assert dams_factor.value == "1 dam" and "2.5 MW" in dams_factor.detail


def test_earth_engine_needs_flow_into_the_report(sources, settings, monkeypatch):
    metrics = {
        "heat_island_c": 9.0,
        "lst_built_up_c": 38.0,
        "lst_natural_c": 29.0,
        "built_up_share": 0.02,
        "canopy_built_up": 0.05,
    }
    monkeypatch.setattr(earthengine, "fetch_county_metrics", returning(metrics))
    report = analyze(settings)
    ids = {n.id for n in report.needs}
    assert {"urban_heat", "low_tree_canopy"} <= ids
    assert any(o.solution_id == "shade_trees_depaving" for o in report.opportunities)
    assert next(s for s in report.source_status if s.source == "earthengine").ok


def test_earth_engine_failure_keeps_the_rest_of_the_report(sources, settings, monkeypatch):
    monkeypatch.setattr(
        earthengine, "fetch_county_metrics", raising(SourceError("Earth Engine sign-in failed"))
    )
    report = analyze(settings)
    status = next(s for s in report.source_status if s.source == "earthengine")
    assert not status.ok and "sign-in" in status.message
    assert report.needs and not {"urban_heat", "low_tree_canopy"} & {n.id for n in report.needs}
