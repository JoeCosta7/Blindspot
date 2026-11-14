import asyncio
import json

import pytest

from app import matching, opportunities
from app.config import BACKEND_DIR
from app.models import Asset, Factor, Highlight
from tests.conftest import make_need

SOLUTIONS = matching.load_solutions(BACKEND_DIR / "data" / "solutions.json")


def site(asset_id: str, asset_type: str, name: str, **attrs) -> Asset:
    return Asset(
        id=asset_id, type=asset_type, name=name, lat=0.5, lon=0.5, attributes=attrs, source="test"
    )


def factor(factor_id: str, category: str, label: str, value: str, **extra) -> Factor:
    return Factor(id=factor_id, category=category, label=label, value=value, source="test", **extra)


FACTORS = [
    factor("solar_resource", "climate", "Solar resource", "1,200 kWh a year per kW of panels"),
    factor(
        "unemployment",
        "workforce",
        "Unemployment rate",
        "9.4%",
        comparison="US: 5.2%",
        detail="Share of the civilian labor force that is out of work and looking for it.",
    ),
    factor(
        "construction_jobs",
        "workforce",
        "Jobs in construction",
        "11.3%",
        comparison="US: 6.9%",
    ),
    factor("housing_pre1980", "buildings", "Homes built before 1980", "70.0%"),
]


def sample_digest(sites_per_type: int = 2):
    needs = [make_need("low_income", 67), make_need("hazard_risk", 100)]
    needs[0] = needs[0].model_copy(
        update={
            "label": "Low household income",
            "value": 60_000.0,
            "national_value": 80_000.0,
            "unit": "usd",
        }
    )
    needs[1] = needs[1].model_copy(
        update={
            "label": "High natural hazard risk",
            "value": None,
            "national_value": None,
            "unit": "rating",
            "detail": "Wildfire: Relatively High",
        }
    )
    assets = [
        # Best first within a type, as the pipeline passes them.
        site("repowering:4242", "brownfield", "Old Mill", acres=69.0, epa_pv_mw=10.0, city="Erie"),
        site("repowering:2", "brownfield", "Rail Yard", acres=40.0, transmission_miles=1.0),
        site("repowering:3", "brownfield", "Tannery", acres=float("nan")),
        site("osm:node/1", "library", "Main Library", city=None),
        site("afdc:1", "ev_station", "Charger A"),
        site("afdc:2", "ev_station", "Charger B"),
        site(
            "lmop:7",
            "lmop_landfill",
            "County Landfill",
            landfill_status="Open",
            is_candidate=True,
            has_operational_project=False,
            waste_in_place_tons=2_000_000.0,
            ownership_type="Public",
        ),
        site(
            "dam:ZZ1",
            "non_powered_dam",
            "Mill Pond Dam",
            capacity_mw=2.5,
            waterway="Mill River",
            owner_type="Local Government",
        ),
    ]
    return opportunities.build_digest(
        "Test County",
        "ZZ",
        needs,
        FACTORS,
        assets,
        ["college", "mine_land"],
        SOLUTIONS,
        sites_per_type,
        standouts=[
            Highlight(
                factor_id="construction_jobs",
                label="Jobs in construction",
                value="11.3%",
                benchmark="US: 6.9%",
                times_better=1.6,
                why="A construction sector that can take on local projects.",
            )
        ],
    )


def item(**overrides):
    base = {
        "title": "Solar for renters at the Old Mill",
        "need_ids": ["low_income", "hazard_risk"],
        "factor_ids": ["solar_resource"],
        "site_ids": ["repowering:4242"],
        "project_type": "community_solar",
        "what": "Lease the 69 acres at Old Mill for a solar project that serves renters.",
        "why_here": "The sun yields 1,200 kWh a year per kW and the site is in Erie.",
        "plan": [
            "Ask the owner about a lease.",
            "Confirm cleanup status with the state.",
            "Size the array from the acreage in this report.",
            "Match funding leads and confirm deadlines.",
            "Issue an RFP and start interconnection talks.",
            "Build, interconnect and enroll subscribers.",
        ],
        "compliance": [
            "Confirm the owner will allow a community solar lease.",
            "Ask the state what cleanup clearances still apply.",
        ],
        "site_plans": [
            {
                "site_id": "repowering:4242",
                "course_of_action": [
                    "Walk the 69 acres at Old Mill with the owner.",
                    "Confirm cleanup status with the state.",
                    "Order a site survey and interconnection study.",
                    "Close the lease and start construction.",
                ],
                "compliance": [
                    "Confirm the owner of Old Mill will lease for solar.",
                    "Ask the state what cleanup clearances still apply at Old Mill.",
                ],
            }
        ],
    }
    return opportunities._ModelOpportunity(**{**base, **overrides})


def model(*items, summary: str = "Test County has 2 needs and the land to answer them."):
    return opportunities.ModelPlan(summary=summary, opportunities=list(items))


# --- digest --------------------------------------------------------------------


def test_digest_has_the_three_parts_and_the_project_types():
    digest = sample_digest()
    assert digest["county"] == "Test County, ZZ"
    assert digest["needs"] == [  # highest priority first; empty fields are left out
        {
            "need_id": "hazard_risk",
            "label": "High natural hazard risk",
            "theme": "energy",
            "level": "high",
            "detail": "Wildfire: Relatively High",
        },
        {
            "need_id": "low_income",
            "label": "Low household income",
            "theme": "energy",
            "level": "high",
            "county_value": "$60,000",
            "national_value": "$80,000",
        },
    ]
    assert digest["factors"][0] == {
        "factor_id": "solar_resource",
        "kind": "climate",
        "label": "Solar resource",
        "value": "1,200 kWh a year per kW of panels",
    }
    assert digest["factors"][1] == {
        "factor_id": "unemployment",
        "kind": "workforce",
        "label": "Unemployment rate",
        "value": "9.4%",
        "comparison": "US: 5.2%",
        "note": "Share of the civilian labor force that is out of work and looking for it.",
    }
    assert digest["not_found"] == ["Colleges", "Abandoned mine lands"]
    types = {p["project_type"]: p for p in digest["project_types"]}
    assert set(types) == {s.id for s in SOLUTIONS}
    assert types["landfill_gas_energy"] == {
        "project_type": "landfill_gas_energy",
        "name": "Landfill gas-to-energy",
        "built_at": ["Landfills (EPA landfill gas database)"],
    }
    assert "built_at" not in types["farm_digester"]  # county-wide: no host site


def test_digest_lists_a_few_sites_per_type_with_exact_counts():
    digest = sample_digest(sites_per_type=2)
    groups = {g["kind"]: g for g in digest["sites"]}

    brownfields = groups["Brownfields"]
    assert brownfields["count"] == 3
    assert [e["site_id"] for e in brownfields["examples"]] == ["repowering:4242", "repowering:2"]
    assert brownfields["examples"][0] == {
        "site_id": "repowering:4242",
        "name": "Old Mill",
        "facts": "69 acres; EPA solar screening estimate 10 MW; in Erie",
    }
    assert brownfields["examples"][1]["facts"] == (
        "40 acres; 1 mile to the nearest transmission line"
    )
    # Chargers are counted, never listed.
    assert groups["Public EV charging stations"] == {
        "kind": "Public EV charging stations",
        "count": 2,
    }
    assert groups["Landfills (EPA landfill gas database)"]["examples"][0]["facts"] == (
        "status: Open; EPA candidate for a gas energy project; "
        "2,000,000 tons of waste in place; ownership: Public"
    )
    assert groups["Dams without hydropower"]["examples"][0]["facts"] == (
        "ORNL modelled hydropower potential 2.5 MW; on Mill River; owner type: Local Government"
    )


def test_a_site_with_no_usable_attribute_carries_no_facts():
    digest = sample_digest(sites_per_type=3)
    brownfields = next(g for g in digest["sites"] if g["kind"] == "Brownfields")
    assert brownfields["examples"][2] == {"site_id": "repowering:3", "name": "Tannery"}


def test_digest_stays_small_however_many_sites_the_county_has():
    def digest_for(count: int):
        assets = [
            site(f"repowering:{i}", "mine_land", f"Mine {i}", acres=float(1000 - i))
            for i in range(count)
        ]
        return opportunities.build_digest(
            "Test County", "ZZ", [make_need("low_income", 50)], [], assets, [], SOLUTIONS, 3
        )

    small, large = digest_for(5), digest_for(5000)
    assert large["sites"][0]["count"] == 5000
    assert len(large["sites"][0]["examples"]) == 3
    assert len(json.dumps(large)) - len(json.dumps(small)) < 10  # only the count differs


def test_identifiers_are_not_figures():
    allowed = opportunities.allowed_numbers(sample_digest())
    assert {69.0, 60_000.0, 9.4, 2.5, 1200.0} <= allowed
    assert 4242.0 not in allowed  # only in a site id
    # "housing_pre1980" is an id, but its label states the year, so 1980 is a figure.
    assert 1980.0 in allowed


# --- validation ------------------------------------------------------------------


def test_a_grounded_opportunity_passes():
    assert opportunities.opportunity_problem(item(), sample_digest()) is None
    # County-wide, built only on factors: two problems that solve each other.
    training = item(
        title="Train residents to repair old homes",
        need_ids=["low_income"],
        factor_ids=["unemployment", "construction_jobs", "housing_pre1980"],
        site_ids=[],
        project_type="other",
        what="Pair residents who are out of work with local builders to repair older homes.",
        why_here="Unemployment is 9.4% and 11.3% of employed residents work in construction.",
    )
    assert opportunities.opportunity_problem(training, sample_digest()) is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"need_ids": []}, "addresses no need"),
        ({"need_ids": ["renters"]}, "need that is not in the input"),  # real id, not flagged
        ({"need_ids": ["made_up"]}, "need that is not in the input"),
        ({"site_ids": ["repowering:999"]}, "site that is not in the input"),
        # In the county, but not among the sites the digest listed (2 per type).
        ({"site_ids": ["repowering:3"]}, "site that is not in the input"),
        # Leverages nothing the county has.
        ({"site_ids": [], "factor_ids": []}, "none of the county's factors or sites"),
        ({"site_ids": [], "factor_ids": ["made_up"]}, "none of the county's factors or sites"),
        ({"what": "Build a 5 MW array that saves $300 a year."}, "numbers that are not"),
        (
            {"plan": ["Hire a consultant within 90 days."], "first_steps": []},
            "numbers that are not",
        ),
        ({"why_here": "About 9% of people are out of work."}, "numbers that are not"),  # rounded
        ({"what": "word " * 80}, "too long"),
        ({"title": " "}, "required field is empty"),
        ({"plan": [], "first_steps": []}, "no plan"),
    ],
)
def test_ungrounded_opportunities_are_rejected(overrides, expected):
    problem = opportunities.opportunity_problem(item(**overrides), sample_digest())
    assert problem is not None and expected in problem


def test_a_small_length_overrun_is_tolerated():
    over_by_a_little = "word " * (opportunities.TEXT_MAX_WORDS + 5)
    assert opportunities.opportunity_problem(item(what=over_by_a_little), sample_digest()) is None


def test_keep_valid_drops_bad_ones_and_keeps_the_rest():
    kept, dropped = opportunities.keep_valid(
        model(
            item(title="First", factor_ids=["solar_resource", "made_up", "solar_resource"]),
            item(title="Bad", what="Creates 150 jobs."),
            item(title="first"),  # repeats the first
            item(
                title="Second",
                need_ids=["hazard_risk", "hazard_risk"],
                site_ids=[],
                project_type="a type of my own",
            ),
            item(title="Third"),
        ),
        sample_digest(),
        limit=2,
    )
    assert [d.title for d in kept] == ["First", "Second"]
    first, second = kept
    assert first.asset_ids == ["repowering:4242"]
    assert first.factor_ids == ["solar_resource"]  # unknown and repeated ids are stripped
    assert first.solution_id == "community_solar"
    assert first.summary.startswith("Lease the 69 acres")
    assert second.need_ids == ["hazard_risk"] and second.asset_ids == []
    assert second.solution_id is None  # not a catalogued project type
    # Bad invents a number; "first" repeats the title; "Third" repeats community_solar.
    assert len(dropped) == 3
    assert "150" in dropped[0] and "repeats" in dropped[1] and "project type" in dropped[2]


def test_summary_is_checked_like_an_opportunity():
    digest = sample_digest()
    assert (
        opportunities.summary_problem("The county has 2 needs; income is $60,000.", digest) is None
    )
    assert "numbers" in opportunities.summary_problem("This will create 150 jobs.", digest)
    assert "too long" in opportunities.summary_problem("word " * 200, digest)
    assert "empty" in opportunities.summary_problem("  ", digest)


# --- prompt ----------------------------------------------------------------------


def test_prompt_carries_the_rules_and_the_digest():
    prompt = opportunities.build_prompt(sample_digest(), 6)
    for rule in opportunities.PROMPT_RULES:
        assert rule in prompt
    assert "Create up to 6 opportunities" in prompt
    assert "repowering:4242" in prompt and "hazard_risk" in prompt
    assert "construction_jobs" in prompt and "community_solar" in prompt
    assert '"standouts"' in prompt and "times_above_typical" in prompt
    assert "generic solar-and-chargers pack" in prompt


def test_digest_includes_standouts():
    digest = sample_digest()
    assert digest["standouts"] == [
        {
            "factor_id": "construction_jobs",
            "label": "Jobs in construction",
            "value": "11.3%",
            "compared_to": "US: 6.9%",
            "times_above_typical": 1.6,
            "why_it_matters": "A construction sector that can take on local projects.",
        }
    ]


def test_keep_valid_drops_a_repeated_project_type():
    kept, dropped = opportunities.keep_valid(
        model(
            item(title="Solar A", project_type="community_solar"),
            item(
                title="Solar B",
                project_type="community_solar",
                site_ids=["repowering:2"],
            ),
            item(
                title="Clinic",
                project_type="weatherization_clinic",
                need_ids=["hazard_risk"],
                site_ids=["osm:node/1"],
                factor_ids=["housing_pre1980"],
            ),
        ),
        sample_digest(),
        limit=6,
    )
    assert [d.title for d in kept] == ["Solar A", "Clinic"]
    assert any("project type" in reason for reason in dropped)


def test_keep_valid_drops_a_third_use_of_the_same_top_need():
    # hazard_risk outranks low_income in the digest, so it is the top need for
    # the first two; a third whose top need is also hazard_risk is dropped.
    kept, dropped = opportunities.keep_valid(
        model(
            item(title="One", project_type="community_solar", need_ids=["hazard_risk"]),
            item(
                title="Two",
                project_type="weatherization_clinic",
                need_ids=["hazard_risk", "low_income"],
                site_ids=["osm:node/1"],
                factor_ids=["housing_pre1980"],
            ),
            item(
                title="Three",
                project_type="other",
                need_ids=["hazard_risk"],
                site_ids=[],
                factor_ids=["unemployment"],
            ),
            item(
                title="Income help",
                project_type="public_ev_charging",
                need_ids=["low_income"],
                site_ids=["osm:node/1"],
                factor_ids=["unemployment"],
            ),
        ),
        sample_digest(),
        limit=6,
    )
    assert [d.title for d in kept] == ["One", "Two", "Income help"]
    assert any("same top need" in reason for reason in dropped)


# --- write_opportunities -----------------------------------------------------------


def with_fake_gemini(monkeypatch, settings, fake):
    monkeypatch.setattr(opportunities, "_call_gemini", fake)
    return settings.model_copy(update={"gemini_api_key": "k", "gemini_timeout_s": 0.05})


def write(digest, settings):
    return asyncio.run(opportunities.write_opportunities(digest, settings))


def test_nothing_is_requested_without_a_key_or_needs(monkeypatch, settings):
    async def unexpected(prompt, s):
        raise AssertionError("Gemini must not be called")

    monkeypatch.setattr(opportunities, "_call_gemini", unexpected)

    plan, status = write(sample_digest(), settings)
    assert plan is None and status.source == "gemini"
    assert status.ok is False and "GEMINI_API_KEY" in status.message

    keyed = settings.model_copy(update={"gemini_api_key": "k"})
    plan, status = write({**sample_digest(), "needs": []}, keyed)
    assert plan is None and "No needs were flagged" in status.message


def test_valid_opportunities_are_used_then_cached(monkeypatch, settings):
    calls = []

    async def fake(prompt, s):
        calls.append(prompt)
        return model(item(title="Good"), item(title="Bad", what="Creates 150 jobs."))

    keyed = with_fake_gemini(monkeypatch, settings, fake)
    first, status1 = write(sample_digest(), keyed)
    second, status2 = write(sample_digest(), keyed)

    assert [d.title for d in first.drafts] == ["Good"] and first == second
    assert first.summary == "Test County has 2 needs and the land to answer them."
    assert (status1.ok, status1.cached, status2.cached) == (True, False, True)
    assert "1 of 2 opportunities dropped" in status1.message
    assert len(calls) == 1

    # A different county digest is a different cache entry.
    write({**sample_digest(), "county": "Other County, ZZ"}, keyed)
    assert len(calls) == 2


def test_a_bad_summary_does_not_cost_the_opportunities(monkeypatch, settings):
    async def fake(prompt, s):
        return model(item(), summary="These projects will create 150 jobs.")

    plan, status = write(sample_digest(), with_fake_gemini(monkeypatch, settings, fake))
    assert plan is not None and plan.summary is None and len(plan.drafts) == 1
    assert status.ok and "summary rejected" in status.message and "150" in status.message


def test_all_rejected_returns_none_and_is_not_cached(monkeypatch, settings):
    async def fake(prompt, s):
        return model(item(what="Creates 150 jobs."))

    keyed = with_fake_gemini(monkeypatch, settings, fake)
    plan, status = write(sample_digest(), keyed)
    assert plan is None and status.ok is False
    assert "rejected" in status.message and "150" in status.message
    assert not list(keyed.cache_dir.glob("gemini_opportunities_*"))


def test_error_timeout_and_empty_answer_return_none(monkeypatch, settings):
    class Overloaded(Exception):
        code = 503
        status = "UNAVAILABLE"

    async def boom(prompt, s):
        raise RuntimeError("quota")

    async def overloaded(prompt, s):
        raise Overloaded("high demand")

    async def slow(prompt, s):
        await asyncio.sleep(1)

    async def nothing(prompt, s):
        return model()

    for fake, expected in (
        (boom, "RuntimeError"),
        (overloaded, "HTTP 503 UNAVAILABLE"),
        (slow, "longer than"),
        (nothing, "it returned none"),
    ):
        keyed = with_fake_gemini(monkeypatch, settings, fake)
        plan, status = write(sample_digest(), keyed)
        assert plan is None and status.ok is False
        assert expected in status.message
