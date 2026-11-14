import asyncio

import pytest

from app import narrative
from app.models import Estimate, FundingMatch, Opportunity
from tests.conftest import make_need


def sample_payload():
    needs = [make_need("low_income", 67), make_need("old_housing", 100)]
    needs[0] = needs[0].model_copy(
        update={
            "label": "Low household income",
            "value": 60_000.0,
            "national_value": 80_000.0,
            "unit": "usd",
        }
    )
    needs[1] = needs[1].model_copy(
        update={"label": "Housing built before 1980", "value": 0.317, "national_value": 0.25}
    )
    opportunity = Opportunity(
        id="community_solar:repowering:123",
        solution_id="community_solar",
        title="Community solar on Old Mill Site",
        asset_id="repowering:123",
        need_ids=["low_income"],
        score=67,
        estimates=[
            Estimate(
                label="Capacity",
                value=6.0,
                unit="MW",
                assumptions=["60% of the site's 69 acres is usable"],
            ),
            Estimate(label="Household equivalent", value=670.0, unit="homes", assumptions=["x"]),
        ],
        funding=[
            FundingMatch(
                program_id="epa_brownfields_fy27",
                name="EPA Brownfields Grants (FY 2027)",
                agency="EPA",
                status="open",
                deadline="2027-01-21",
                key_terms="Cleanup grants capped at $500,000.",
                verified_on="2026-11-13",
                source_url="https://example.org",
            )
        ],
    )
    return narrative.build_payload("Test County", "ZZ", needs, [opportunity])


def model_narrative(summary: str, note: str = "A good site.", caveats=None):
    return narrative.ModelNarrative(
        summary=summary,
        opportunity_notes=[{"opportunity_id": "community_solar:repowering:123", "text": note}],
        caveats=caveats or ["Confirm the deadline."],
    )


# --- payload ------------------------------------------------------------------


def test_payload_is_compact_and_display_ready():
    payload = sample_payload()
    assert payload["county"] == "Test County, ZZ"
    assert payload["needs"][0]["label"] == "Housing built before 1980"  # most severe first
    assert payload["needs"][0]["county_value"] == "31.7%"
    assert payload["needs"][1]["county_value"] == "$60,000"
    opportunity = payload["opportunities"][0]
    assert opportunity["addresses"] == ["Low household income"]
    assert opportunity["estimates"][0]["value"] == "6 MW"
    assert opportunity["funding"] == [
        {"name": "EPA Brownfields Grants (FY 2027)", "status": "open", "deadline": "2027-01-21"}
    ]
    # Key terms, scores and URLs are stripped: the model has no use for them.
    assert "500,000" not in str(payload)


def test_extract_numbers():
    text = "About 1,234 homes, 31.7% of them, by 2027-01-21 (see 48E)."
    assert narrative.extract_numbers(text) == {1234.0, 31.7, 2027.0, 1.0, 21.0, 48.0}


# --- validation -----------------------------------------------------------------


def test_numbers_from_the_input_pass():
    payload = sample_payload()
    ok = model_narrative(
        "Test County has 2 needs. 31.7% of homes were built before 1980, against 25.0% nationally.",
        "An estimated 6 MW could serve an estimated 670 homes. "
        "EPA Brownfields is open until January 21, 2027.",
    )
    assert narrative.validation_error(ok, payload) is None


@pytest.mark.parametrize(
    "summary",
    [
        "About 32% of homes are old.",  # rounded
        "This could create 150 jobs.",  # invented
        "Income is $20,000 below the national figure.",  # calculated
    ],
)
def test_invented_numbers_are_rejected(summary):
    problem = narrative.validation_error(model_narrative(summary), sample_payload())
    assert problem is not None and "not in the input" in problem


def test_invented_number_in_caveat_is_rejected():
    bad = model_narrative("Fine.", caveats=["Grants are capped at $500,000."])
    assert narrative.validation_error(bad, sample_payload()) is not None


def test_length_and_unknown_opportunity_are_rejected():
    payload = sample_payload()
    assert "over 90 words" in narrative.validation_error(model_narrative("word " * 91), payload)
    assert "over 60 words" in narrative.validation_error(
        model_narrative("Fine.", "word " * 61), payload
    )
    stranger = narrative.ModelNarrative(
        summary="Fine.", opportunity_notes=[{"opportunity_id": "made:up", "text": "x"}], caveats=[]
    )
    assert "not in the input" in narrative.validation_error(stranger, payload)


# --- template -------------------------------------------------------------------


def test_template_passes_its_own_validation():
    payload = sample_payload()
    brief = narrative.template_narrative(payload, ["Census ACS (county)"])

    assert brief.generated_by == "template"
    assert "Test County, ZZ has 2 flagged needs" in brief.summary
    assert "Community solar on Old Mill Site" in brief.summary
    note = brief.opportunity_notes[0].text
    assert "capacity 6 MW" in note
    assert "EPA Brownfields Grants (FY 2027) (open, deadline 2027-01-21)" in note
    assert any("Census ACS (county)" in c for c in brief.caveats)
    assert any("estimates are rough" in c for c in brief.caveats)

    as_model = narrative.ModelNarrative(
        summary=brief.summary,
        opportunity_notes=[n.model_dump() for n in brief.opportunity_notes],
        caveats=brief.caveats,
    )
    assert narrative.validation_error(as_model, payload) is None


def test_template_with_nothing_found():
    payload = narrative.build_payload("Empty County", "ZZ", [], [])
    brief = narrative.template_narrative(payload, [])
    assert brief.summary == "No needs were flagged for Empty County, ZZ from the data that loaded."
    assert brief.opportunity_notes == [] and brief.caveats == []


def test_prompt_contains_the_five_rules_verbatim():
    prompt = narrative.build_prompt(sample_payload())
    for rule in narrative.PROMPT_RULES:
        assert rule in prompt
    assert "community_solar:repowering:123" in prompt


# --- write_narrative ------------------------------------------------------------


def test_no_key_uses_the_template(settings):
    brief, status = asyncio.run(narrative.write_narrative(sample_payload(), [], settings))
    assert brief.generated_by == "template"
    assert status.ok is False and "GEMINI_API_KEY" in status.message


def with_fake_gemini(monkeypatch, settings, fake):
    monkeypatch.setattr(narrative, "_call_gemini", fake)
    return settings.model_copy(update={"gemini_api_key": "k", "gemini_timeout_s": 0.05})


def test_valid_gemini_output_is_used_then_cached(monkeypatch, settings):
    calls = []

    async def fake(prompt, s):
        calls.append(prompt)
        return model_narrative("Test County has 2 flagged needs.")

    keyed = with_fake_gemini(monkeypatch, settings, fake)
    first, status1 = asyncio.run(narrative.write_narrative(sample_payload(), ["PVWatts"], keyed))
    second, status2 = asyncio.run(narrative.write_narrative(sample_payload(), [], keyed))

    assert first.generated_by == second.generated_by == "gemini"
    assert first.summary == "Test County has 2 flagged needs."
    assert (status1.cached, status2.cached) == (False, True)
    assert len(calls) == 1
    # Failed sources are always stated by code, not left to the model.
    assert any("PVWatts" in c for c in first.caveats)
    assert not any("PVWatts" in c for c in second.caveats)


def test_invented_number_falls_back_and_is_not_cached(monkeypatch, settings):
    async def fake(prompt, s):
        return model_narrative("This will create 150 jobs.")

    keyed = with_fake_gemini(monkeypatch, settings, fake)
    brief, status = asyncio.run(narrative.write_narrative(sample_payload(), [], keyed))
    assert brief.generated_by == "template"
    assert "rejected" in status.message
    assert not list(keyed.cache_dir.glob("gemini_*"))


def test_error_and_timeout_fall_back(monkeypatch, settings):
    async def boom(prompt, s):
        raise RuntimeError("quota")

    async def slow(prompt, s):
        await asyncio.sleep(1)

    for fake, expected in ((boom, "RuntimeError"), (slow, "longer than")):
        keyed = with_fake_gemini(monkeypatch, settings, fake)
        brief, status = asyncio.run(narrative.write_narrative(sample_payload(), [], keyed))
        assert brief.generated_by == "template"
        assert expected in status.message
