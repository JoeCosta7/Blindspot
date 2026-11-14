import asyncio
import json

import pytest

from app import suggestions
from app.models import Asset, CountyContext, Estimate, Opportunity, Resources
from tests.conftest import make_need


def site(asset_id: str, asset_type: str, name: str, **attrs) -> Asset:
    return Asset(
        id=asset_id, type=asset_type, name=name, lat=0.5, lon=0.5, attributes=attrs, source="test"
    )


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
        site("geoapify:p1", "library", "Main Library", city=None),
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
    opportunity = Opportunity(
        id="community_solar:repowering:3",
        solution_id="community_solar",
        title="Community solar on Tannery",
        asset_id="repowering:3",
        need_ids=["low_income"],
        score=47,
        estimates=[Estimate(label="Capacity", value=6.0, unit="MW", assumptions=["x"])],
        funding=[],
    )
    return suggestions.build_digest(
        "Test County",
        "ZZ",
        20_000,
        CountyContext(median_household_income=60_000.0, poverty_rate=0.151),
        Resources(solar_kwh_per_kw_year=1200.0),
        needs,
        assets,
        ["college", "mine_land"],
        [opportunity],
        sites_per_type,
    )


def solution(**overrides):
    base = {
        "title": "Flood-safe solar at the Old Mill",
        "need_ids": ["low_income", "hazard_risk"],
        "site_ids": ["repowering:4242"],
        "what": "Lease the 69 acres at Old Mill for a solar project that serves renters.",
        "why_here": "Median household income is $60,000 and the site is in Erie.",
        "first_steps": ["Ask the owner about a lease.", "Confirm cleanup status with the state."],
    }
    return suggestions._ModelSolution(**{**base, **overrides})


def model(*solutions):
    return suggestions.ModelSuggestions(solutions=list(solutions))


# --- digest --------------------------------------------------------------------


def test_digest_lists_a_few_sites_per_type_with_exact_counts():
    digest = sample_digest(sites_per_type=2)
    groups = {g["kind"]: g for g in digest["sites"]}

    brownfields = groups["Brownfields"]
    assert brownfields["count"] == 3
    # The site an opportunity already uses comes first, then best first.
    assert [e["site_id"] for e in brownfields["examples"]] == ["repowering:3", "repowering:4242"]
    assert brownfields["examples"][1] == {
        "site_id": "repowering:4242",
        "name": "Old Mill",
        "facts": "69 acres; EPA solar screening estimate 10 MW; in Erie",
    }
    # A site with no usable attribute (NaN acres) carries no facts line.
    assert brownfields["examples"][0] == {"site_id": "repowering:3", "name": "Tannery"}

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
    assert digest["not_found"] == ["Colleges", "Abandoned mine lands"]


def test_digest_values_are_display_ready():
    digest = sample_digest()
    assert digest["county"] == "Test County, ZZ"
    assert digest["figures"] == {  # unknown figures are left out
        "Population": "20,000",
        "Median household income": "$60,000",
        "Poverty rate": "15.1%",
        "Solar yield": "1,200 kWh a year per kW of panels",
    }
    assert digest["needs"] == [  # most severe first; empty fields are left out
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
    assert digest["already_in_report"] == [
        {"project": "Community solar on Tannery", "estimates": ["Capacity: 6 MW"]}
    ]


def test_digest_stays_small_however_many_sites_the_county_has():
    def digest_for(count: int):
        assets = [
            site(f"repowering:{i}", "mine_land", f"Mine {i}", acres=float(1000 - i))
            for i in range(count)
        ]
        return suggestions.build_digest(
            "Test County",
            "ZZ",
            None,
            CountyContext(),
            Resources(),
            [make_need("low_income", 50)],
            assets,
            [],
            [],
            3,
        )

    small, large = digest_for(5), digest_for(5000)
    assert large["sites"][0]["count"] == 5000
    assert len(large["sites"][0]["examples"]) == 3
    assert len(json.dumps(large)) - len(json.dumps(small)) < 10  # only the count differs


def test_site_ids_are_not_figures():
    allowed = suggestions.allowed_numbers(sample_digest())
    assert {69.0, 60_000.0, 15.1, 2.5} <= allowed
    assert 4242.0 not in allowed


# --- validation ------------------------------------------------------------------


def test_a_grounded_solution_passes():
    assert suggestions.solution_problem(solution(), sample_digest()) is None
    county_wide = solution(site_ids=[], what="Run a county-wide home repair program.")
    assert suggestions.solution_problem(county_wide, sample_digest()) is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"need_ids": []}, "addresses no need"),
        ({"need_ids": ["renters"]}, "need that is not in the input"),  # real id, not flagged
        ({"need_ids": ["made_up"]}, "need that is not in the input"),
        ({"site_ids": ["repowering:999"]}, "site that is not in the input"),
        # In the county, but not among the sites the digest listed (2 per type).
        ({"site_ids": ["repowering:2"]}, "site that is not in the input"),
        ({"what": "Build a 5 MW array that saves $300 a year."}, "numbers that are not"),
        ({"first_steps": ["Hire a consultant within 90 days."]}, "numbers that are not"),
        ({"why_here": "About 15% of people are poor."}, "numbers that are not"),  # rounded
        ({"what": "word " * 70}, "too long"),
        ({"title": " "}, "required field is empty"),
        ({"first_steps": []}, "no first steps"),
    ],
)
def test_ungrounded_solutions_are_rejected(overrides, expected):
    problem = suggestions.solution_problem(solution(**overrides), sample_digest())
    assert problem is not None and expected in problem


def test_a_small_length_overrun_is_tolerated():
    over_by_a_little = "word " * (suggestions.TEXT_MAX_WORDS + 5)
    assert suggestions.solution_problem(solution(what=over_by_a_little), sample_digest()) is None


def test_keep_valid_drops_bad_ones_and_keeps_the_rest():
    kept, dropped = suggestions.keep_valid(
        model(
            solution(title="First"),
            solution(title="Bad", what="Creates 150 jobs."),
            solution(title="first"),  # repeats the first
            solution(title="Second", need_ids=["hazard_risk", "hazard_risk"], site_ids=[]),
            solution(title="Third"),
        ),
        sample_digest(),
        limit=2,
    )
    assert [(s.id, s.title) for s in kept] == [("ai:1", "First"), ("ai:2", "Second")]
    assert kept[1].need_ids == ["hazard_risk"] and kept[1].asset_ids == []
    assert kept[0].asset_ids == ["repowering:4242"]
    assert kept[0].summary.startswith("Lease the 69 acres")
    assert len(dropped) == 2 and "150" in dropped[0] and "repeats" in dropped[1]


# --- prompt ----------------------------------------------------------------------


def test_prompt_carries_the_rules_and_the_digest():
    prompt = suggestions.build_prompt(sample_digest(), 4)
    for rule in suggestions.PROMPT_RULES:
        assert rule in prompt
    assert "up to 4 practical solutions" in prompt
    assert "repowering:4242" in prompt and "hazard_risk" in prompt


# --- suggest_solutions -------------------------------------------------------------


def with_fake_gemini(monkeypatch, settings, fake):
    monkeypatch.setattr(suggestions, "_call_gemini", fake)
    return settings.model_copy(update={"gemini_api_key": "k", "gemini_timeout_s": 0.05})


def suggest(digest, settings):
    return asyncio.run(suggestions.suggest_solutions(digest, settings))


def test_nothing_is_requested_without_a_key_or_needs(monkeypatch, settings):
    async def unexpected(prompt, s):
        raise AssertionError("Gemini must not be called")

    monkeypatch.setattr(suggestions, "_call_gemini", unexpected)

    found, status = suggest(sample_digest(), settings)
    assert found == [] and status.source == "gemini_solutions"
    assert status.ok is False and "GEMINI_API_KEY" in status.message

    keyed = settings.model_copy(update={"gemini_api_key": "k"})
    empty = {**sample_digest(), "needs": []}
    found, status = suggest(empty, keyed)
    assert found == [] and "No needs were flagged" in status.message

    found, status = suggest(sample_digest(), keyed.model_copy(update={"max_ai_solutions": 0}))
    assert found == [] and "turned off" in status.message


def test_valid_suggestions_are_used_then_cached(monkeypatch, settings):
    calls = []

    async def fake(prompt, s):
        calls.append(prompt)
        return model(solution(title="Good"), solution(title="Bad", what="Creates 150 jobs."))

    keyed = with_fake_gemini(monkeypatch, settings, fake)
    first, status1 = suggest(sample_digest(), keyed)
    second, status2 = suggest(sample_digest(), keyed)

    assert [s.title for s in first] == ["Good"] and first == second
    assert (status1.ok, status1.cached, status2.cached) == (True, False, True)
    assert "1 of 2 suggestions dropped" in status1.message
    assert len(calls) == 1

    # A different county digest is a different cache entry.
    other = {**sample_digest(), "county": "Other County, ZZ"}
    suggest(other, keyed)
    assert len(calls) == 2


def test_all_rejected_returns_none_and_is_not_cached(monkeypatch, settings):
    async def fake(prompt, s):
        return model(solution(what="Creates 150 jobs."))

    keyed = with_fake_gemini(monkeypatch, settings, fake)
    found, status = suggest(sample_digest(), keyed)
    assert found == [] and status.ok is False
    assert "rejected" in status.message and "150" in status.message
    assert not list(keyed.cache_dir.glob("gemini_solutions_*"))


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
        found, status = suggest(sample_digest(), keyed)
        assert found == [] and status.ok is False
        assert expected in status.message


def test_digest_includes_profile_and_its_numbers_are_allowed():
    from app.models import ProfileFact

    fact = ProfileFact(
        id="trades_workers",
        category="workforce",
        label="Workers in construction, maintenance and natural resource jobs",
        value=0.183,
        national_value=0.087,
        unit="share",
        source="ACS 2020-2024",
    )
    digest = suggestions.build_digest(
        "Test County", "ZZ", 20_000, CountyContext(), Resources(), [], [], [], [], 2, [fact]
    )
    assert digest["profile"] == [
        {
            "label": "Workers in construction, maintenance and natural resource jobs",
            "category": "workforce",
            "county_value": "18.3%",
            "national_value": "8.7%",
        }
    ]
    assert {18.3, 8.7} <= suggestions.allowed_numbers(digest)
