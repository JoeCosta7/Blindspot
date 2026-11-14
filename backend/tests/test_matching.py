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
    }
    assert {s.id for s in SOLUTIONS} == {
        "community_solar",
        "resilience_hub",
        "weatherization_clinic",
        "public_ev_charging",
        "landfill_gas_energy",
    }
    for solution in SOLUTIONS:
        assert set(solution.triggers) <= need_ids, solution.id
        assert set(solution.hosts) <= asset_types, solution.id


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

    # Nine candidates for six places: all five solutions appear, plus the
    # best second site, and the list is still ordered by score.
    assert {m.solution.id for m in matches} == {s.id for s in SOLUTIONS}
    assert [m.score for m in matches] == [100, 100, 90, 85, 85, 50]
    assert matches[-1].solution.id == "landfill_gas_energy"


# --- estimates ------------------------------------------------------------------


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
