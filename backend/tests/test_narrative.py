from app import narrative
from app.models import Estimate, FundingMatch, Opportunity
from tests.conftest import make_need


def sample_payload(generated_by: str = "rules"):
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
        asset_ids=["repowering:123"],
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
        generated_by=generated_by,
    )
    return narrative.build_payload("Test County", "ZZ", needs, [opportunity])


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
    # Key terms, scores and URLs are stripped.
    assert "500,000" not in str(payload)
    assert payload["written_by_gemini"] is False


def test_extract_numbers():
    text = "About 1,234 homes, 31.7% of them, by 2027-01-21 (see 48E)."
    assert narrative.extract_numbers(text) == {1234.0, 31.7, 2027.0, 1.0, 21.0, 48.0}


# --- template -------------------------------------------------------------------


def test_template_uses_only_numbers_from_the_payload():
    payload = sample_payload()
    brief = narrative.template_narrative(payload, ["Census ACS (county)"])

    assert brief.generated_by == "template"
    assert "Test County, ZZ has 2 flagged needs" in brief.summary
    assert '1 opportunity was matched to these needs: "Community solar on Old Mill Site"' in (
        brief.summary
    )
    note = brief.opportunity_notes[0].text
    assert "capacity 6 MW" in note
    assert "EPA Brownfields Grants (FY 2027) (open, deadline 2027-01-21)" in note
    assert any("Census ACS (county)" in c for c in brief.caveats)
    assert any("estimates are rough" in c for c in brief.caveats)
    assert narrative.AI_CAVEAT not in brief.caveats

    written = " ".join([brief.summary, note])
    assert narrative.extract_numbers(written) <= narrative.extract_numbers(str(payload))


def test_template_with_nothing_found():
    payload = narrative.build_payload("Empty County", "ZZ", [], [])
    brief = narrative.template_narrative(payload, [])
    assert brief.summary == "No needs were flagged for Empty County, ZZ from the data that loaded."
    assert brief.opportunity_notes == [] and brief.caveats == []


# --- compose ----------------------------------------------------------------------


def test_compose_uses_geminis_summary_with_caveats_written_by_code():
    payload = sample_payload(generated_by="gemini")
    brief = narrative.compose(payload, ["PVWatts solar yield"], "Gemini's checked summary.")

    assert (brief.generated_by, brief.summary) == ("gemini", "Gemini's checked summary.")
    assert brief.opportunity_notes == []  # a Gemini opportunity carries its own text
    assert brief.caveats[0] == narrative.AI_CAVEAT
    assert any("PVWatts solar yield" in c for c in brief.caveats)
    assert any("funding program is a lead" in c for c in brief.caveats)


def test_compose_falls_back_to_the_template_summary():
    # Gemini wrote the opportunities but its summary was rejected.
    brief = narrative.compose(sample_payload(generated_by="gemini"), [], None)
    assert brief.generated_by == "template"
    assert "1 opportunity was written for these needs" in brief.summary
    assert narrative.AI_CAVEAT in brief.caveats

    # Rule matches: no Gemini text anywhere.
    brief = narrative.compose(sample_payload(), [], None)
    assert "matched to these needs" in brief.summary
    assert narrative.AI_CAVEAT not in brief.caveats
