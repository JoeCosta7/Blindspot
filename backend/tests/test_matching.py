import json

import pytest

from app import estimates, funding, matching
from app.config import BACKEND_DIR
from app.models import NeedId
from tests.conftest import make_asset, make_need

DATA = BACKEND_DIR / "data"
SOLUTIONS = matching.load_solutions(DATA / "solutions.json")
PROGRAMS = funding.load_programs(DATA / "funding_programs.json")
CENTRE = (0.5, 0.5)
MVP_SOLUTIONS = {
    "community_solar",
    "resilience_hub",
    "weatherization_clinic",
    "public_ev_charging",
    "landfill_gas_energy",
}
STRETCH_SOLUTIONS = {"small_hydro", "wind_on_reclaimed_land", "farm_digester"}


def run(needs, assets, **kwargs):
    return matching.match_opportunities(needs, assets, SOLUTIONS, CENTRE, **kwargs)


# --- the hand-written tables --------------------------------------------------


def test_solutions_table_is_consistent_with_the_contract():
    need_ids = set(NeedId.__args__)
    asset_types = {
        "brownfield",
        "contaminated_site",
        "landfill",
        "mine_land",
        "lmop_landfill",
        "school",
        "library",
        "college",
        "non_powered_dam",
    }
    assert {s.id for s in SOLUTIONS} == {*MVP_SOLUTIONS, *STRETCH_SOLUTIONS}
    for solution in SOLUTIONS:
        assert set(solution.triggers) <= need_ids, solution.id
        assert set(solution.hosts) <= asset_types, solution.id
        # Only a county-wide solution may have no host.
        assert bool(solution.hosts) == (solution.asset_kind != "county"), solution.id


def test_funding_table_refers_only_to_known_solutions():
    solution_ids = {s.id for s in SOLUTIONS}
    raw = json.loads((DATA / "funding_programs.json").read_text())
    assert len({p["id"] for p in raw}) == len(raw)
    for program in PROGRAMS:
        assert set(program.eligible_solutions) <= solution_ids, program.id
        if program.status in ("open", "open_with_deadlines", "paused", "uncertain"):
            assert program.verified_on and program.source_url, program.id


# --- matching -------------------------------------------------------------------


def test_no_flagged_trigger_means_no_opportunity():
    assets = [make_asset("a", "brownfield", acres=50.0)]
    assert run([make_need("old_housing", 80)], assets) == []


def test_no_host_asset_means_no_opportunity():
    assert run([make_need("low_income", 80)], [make_asset("s", "ev_station")]) == []


def test_score_is_mean_severity_times_asset_factor():
    needs = [make_need("low_income", 80), make_need("renters", 40), make_need("old_housing", 100)]
    assets = [
        make_asset("big", "brownfield", acres=50.0, pv_scale="utility"),
        make_asset("unsized", "brownfield", pv_scale="unknown_acreage"),
    ]
    matches = [m for m in run(needs, assets) if m.solution.id == "community_solar"]

    assert [m.asset.id for m in matches] == ["big", "unsized"]
    assert [n.id for n in matches[0].needs] == ["low_income", "renters"]
    assert matches[0].score == 60  # mean(80, 40) * 1.0
    assert matches[1].score == 42  # mean(80, 40) * 0.7
    assert matches[0].id == "community_solar:big"
    assert matches[0].title == "Community solar on Site big"


def test_land_prefers_screened_sites_then_acreage():
    solar = next(s for s in SOLUTIONS if s.id == "community_solar")
    assets = [
        make_asset("huge_unscreened", "mine_land", acres=9000.0),
        make_asset("small_utility", "brownfield", acres=40.0, pv_scale="utility"),
        make_asset("large_utility", "landfill", acres=120.0, pv_scale="utility"),
        make_asset("distributed", "brownfield", acres=10.0, pv_scale="distributed"),
    ]
    picked = matching.pick_assets(solar, assets, CENTRE, 3)
    assert [a.id for a in picked] == ["large_utility", "small_utility", "distributed"]


def test_buildings_prefer_host_order_then_distance():
    clinic = next(s for s in SOLUTIONS if s.id == "weatherization_clinic")
    assets = [
        make_asset("near_school", "school", lon=0.5, lat=0.5),
        make_asset("far_library", "library", lon=0.9, lat=0.9),
        make_asset("near_library", "library", lon=0.55, lat=0.5),
        make_asset("college", "college", lon=0.5, lat=0.5),  # not a host for the clinic
    ]
    picked = matching.pick_assets(clinic, assets, CENTRE, 3)
    assert [a.id for a in picked] == ["near_library", "far_library", "near_school"]


def test_landfill_gas_needs_a_candidate_landfill():
    needs = [make_need("landfill_gas_uncaptured", 75)]
    operating = make_asset("lmop:1", "lmop_landfill", is_candidate=False, waste_in_place_tons=5e6)
    candidate = make_asset("lmop:2", "lmop_landfill", is_candidate=True, waste_in_place_tons=2e6)

    assert run(needs, [operating]) == []
    matches = run(needs, [operating, candidate])
    assert [m.asset.id for m in matches] == ["lmop:2"]
    assert matches[0].score == 75


def test_top_six_and_first_choice_assets_rank_first():
    needs = [
        make_need("low_income", 90),
        make_need("hazard_risk", 90),
        make_need("old_housing", 90),
        make_need("ev_charging_gap", 90),
    ]
    assets = [
        make_asset("b1", "brownfield", acres=60.0, pv_scale="utility"),
        make_asset("b2", "brownfield", acres=40.0, pv_scale="utility"),
        make_asset("school1", "school", lon=0.5, lat=0.5),
        make_asset("school2", "school", lon=0.6, lat=0.6),
        make_asset("lib1", "library", lon=0.5, lat=0.5),
        make_asset("lib2", "library", lon=0.7, lat=0.7),
    ]
    matches = run(needs, assets)
    assert len(matches) == 6
    # Equal scores: every solution's first-choice asset comes before any second choice.
    assert {m.solution.id for m in matches[:4]} == {
        "community_solar",
        "resilience_hub",
        "weatherization_clinic",
        "public_ev_charging",
    }
    assert len(run(needs, assets, max_opportunities=3)) == 3


def test_second_sites_do_not_crowd_out_a_lower_scoring_solution():
    needs = [
        make_need("ev_charging_gap", 100),
        make_need("old_housing", 90),
        make_need("hazard_risk", 85),
        make_need("low_income", 85),
        make_need("landfill_gas_uncaptured", 50),
    ]
    assets = [
        make_asset("b1", "brownfield", acres=60.0, pv_scale="utility"),
        make_asset("b2", "brownfield", acres=40.0, pv_scale="utility"),
        make_asset("school1", "school"),
        make_asset("school2", "school", lon=0.6),
        make_asset("lib1", "library"),
        make_asset("lib2", "library", lon=0.6),
        make_asset("lmop:1", "lmop_landfill", is_candidate=True, waste_in_place_tons=2e6),
    ]
    matches = run(needs, assets)

    # Nine candidates for six places: all five triggered solutions appear,
    # plus the best second site, and the list is still ordered by score.
    assert {m.solution.id for m in matches} == MVP_SOLUTIONS
    assert [m.score for m in matches] == [100, 100, 90, 85, 85, 50]
    assert matches[-1].solution.id == "landfill_gas_energy"


def test_dam_and_wind_hosts_are_ranked_by_size():
    needs = [make_need("carbon_intensive_grid", 80)]
    assets = [
        make_asset("dam:small", "non_powered_dam", capacity_mw=0.2),
        make_asset("dam:big", "non_powered_dam", capacity_mw=4.0),
        make_asset("mine", "mine_land", acres=4000.0, epa_wind_mw=50.0),
        make_asset("mine2", "mine_land", acres=9000.0),  # EPA gave no wind estimate
    ]
    matches = run(needs, assets, assets_per_solution=1)
    by_solution = {m.solution.id: m for m in matches}

    assert by_solution["small_hydro"].asset.id == "dam:big"
    assert by_solution["wind_on_reclaimed_land"].asset.id == "mine"
    assert by_solution["small_hydro"].score == 80
    # Community solar is also triggered and takes the larger site.
    assert by_solution["community_solar"].asset.id == "mine2"


def test_wind_needs_a_site_epa_sized_for_wind():
    needs = [make_need("high_electricity_cost", 60)]
    assets = [make_asset("lf", "landfill", acres=500.0, wind_scale="facility")]
    assert "wind_on_reclaimed_land" not in {m.solution.id for m in run(needs, assets)}


def test_county_wide_solution_has_no_asset():
    (match,) = run([make_need("unmanaged_farm_waste", 75)], [])
    assert match.solution.id == "farm_digester"
    assert match.asset is None
    assert match.id == "farm_digester:county"
    assert match.title == "Manure digesters at the county's large livestock farms"
    assert match.score == 52  # 75 * 0.7: there is no site to size
    assert [f.program_id for f in funding.match_funding(PROGRAMS, "farm_digester", None)] == [
        "usda_reap"
    ]


# --- estimates ------------------------------------------------------------------


def test_hydro_estimates_come_from_the_ornl_figures():
    figures = estimates.hydro_at_dam(
        {
            "capacity_mw": 6.058,
            "generation_mwh_yr": 20450.149,
            "input_data": "Gage Flow, Historical Head",
        }
    )
    by_label = {e.label: e for e in figures}
    assert by_label["Capacity"].value == pytest.approx(6.06)
    assert by_label["Annual energy"].value == pytest.approx(20500.0)
    assert by_label["Household equivalent"].value == pytest.approx(1900.0)  # 20,450 MWh / 10,791
    assert all(e.is_estimate and e.assumptions for e in figures)
    assert any("not an engineering design" in a for a in by_label["Capacity"].assumptions)
    assert estimates.hydro_at_dam({"capacity_mw": None}) == []


def test_wind_estimate_is_epas_figure_only():
    (capacity,) = estimates.wind_on_land({"epa_wind_mw": 50.0, "acres": 4000.0})
    assert (capacity.label, capacity.value, capacity.unit) == ("Capacity", 50.0, "MW")
    assert any("80 acres per MW" in a for a in capacity.assumptions)
    capped = estimates.wind_on_land({"epa_wind_mw": 1600.0})[0]
    assert any("Capped at 1,600 MW" in a for a in capped.assumptions)
    assert estimates.wind_on_land({"epa_wind_mw": None}) == []


def test_solar_on_land_formula_and_labels():
    figures = estimates.solar_on_land(69.0, 1200.0, site_specific_yield=True)
    by_label = {e.label: e for e in figures}

    assert by_label["Capacity"].value == pytest.approx(6.0)  # 69 * 0.6 / 6.9
    assert by_label["Capacity"].unit == "MW"
    assert by_label["Annual energy"].value == pytest.approx(7200.0)  # 6,000 kW * 1,200
    assert by_label["Household equivalent"].value == pytest.approx(670.0)  # 7.2 GWh / 10,791
    for estimate in figures:
        assert estimate.is_estimate is True
        assert estimate.assumptions
    assert any("6.9 acres per MW" in a for a in by_label["Capacity"].assumptions)
    assert any("at the site" in a for a in by_label["Annual energy"].assumptions)


def test_solar_without_yield_or_acres():
    assert [e.label for e in estimates.solar_on_land(69.0, None, False)] == ["Capacity"]
    assert estimates.solar_on_land(None, 1200.0, False) == []
    assert estimates.solar_on_land(0.0, 1200.0, False) == []


def test_solar_is_capped_and_says_so():
    capacity = estimates.solar_on_land(2_669_225.0, None, False)[0]
    assert capacity.value == 600.0
    assert any("Capped at 600 MW" in a for a in capacity.assumptions)
    assert any("2,670,000 acres" in a for a in capacity.assumptions)


def test_resilience_hub_is_a_fixed_illustrative_size():
    figures = estimates.resilience_hub(1200.0, site_specific_yield=False)
    assert figures[0].value == 100.0 and figures[0].unit == "kW"
    assert figures[1].value == pytest.approx(120.0)  # MWh per year
    assert any("county center" in a for a in figures[1].assumptions)


def test_landfill_gas_reports_only_what_epa_reports():
    figures = estimates.landfill_gas(
        {
            "lfg_collected_mmscfd": 0.614,
            "lfg_collected_year": 2022.0,
            "waste_in_place_tons": 1008308,
        }
    )
    assert [(e.label, e.value) for e in figures] == [
        ("Landfill gas collected", 0.614),
        ("Waste in place", 1_010_000.0),
    ]
    assert "2022" in figures[0].assumptions[0]
    assert estimates.landfill_gas({}) == []


def test_format_number_never_uses_exponents():
    assert estimates.format_number(2_670_000.0) == "2,670,000"
    assert estimates.format_number(4.25) == "4.25"
    assert estimates.format_number(0.614) == "0.614"


# --- funding --------------------------------------------------------------------


def test_brownfields_grant_only_matches_brownfields():
    on_brownfield = funding.match_funding(PROGRAMS, "community_solar", "brownfield")
    on_mine = funding.match_funding(PROGRAMS, "community_solar", "mine_land")

    assert on_brownfield[0].program_id == "epa_brownfields_fy27"  # open sorts first
    assert on_brownfield[0].deadline == "2027-01-21"
    assert "epa_brownfields_fy27" not in {f.program_id for f in on_mine}
    assert {f.program_id for f in on_mine} >= {"elective_pay_48e_45y", "usda_reap"}


def test_funding_is_sorted_by_status():
    matches = funding.match_funding(PROGRAMS, "community_solar", "brownfield")
    ranks = [funding.STATUS_ORDER[f.status] for f in matches]
    assert ranks == sorted(ranks)


def test_solutions_without_a_curated_program_get_none():
    assert funding.match_funding(PROGRAMS, "public_ev_charging", "library") == []
    assert funding.match_funding(PROGRAMS, "landfill_gas_energy", "lmop_landfill") == []
