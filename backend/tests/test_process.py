"""The government process plan: template steps, site steps, documents, pack."""

import asyncio

from app import process
from app.models import Opportunity, ProcessRequest, SiteActionPlan
from tests.conftest import make_asset, make_need


def opportunity(**kw) -> Opportunity:
    base = dict(
        id="o1",
        solution_id="landfill_composting",
        title="Food-scrap composting at a landfill",
        need_ids=["landfill_gas_uncaptured"],
        score=75,
        estimates=[],
        funding=[],
        generated_by="rules",
        plan=["Survey food-scrap sources"],
    )
    base.update(kw)
    return Opportunity(**base)


def request(**kw) -> ProcessRequest:
    base = dict(
        fips="48201",
        county_name="Harris County",
        state="TX",
        opportunity=opportunity(),
        needs=[make_need("landfill_gas_uncaptured", 75)],
    )
    base.update(kw)
    return ProcessRequest(**base)


def test_site_steps_only_with_a_site_and_type_steps_merge(settings):
    county_wide = asyncio.run(process.build_process(request(), settings))
    ids = {s.id for p in county_wide.phases for s in p.steps}
    assert "compost_permit" in ids and "brief_board" in ids  # type step + default
    assert "confirm_owner" not in ids and "operator_agreement" not in ids  # site-only
    # The opportunity's own plan is kept apart as ideas, not a checklist phase.
    assert all(p.id != "specific" for p in county_wide.phases)
    assert [i.title for i in county_wide.ideas] == ["Survey food-scrap sources"]

    site = make_asset("lmop:7", "lmop_landfill")
    plan = SiteActionPlan(site_id="lmop:7", course_of_action=["Walk the cell with the operator"])
    with_site = asyncio.run(
        process.build_process(
            request(opportunity=opportunity(site_plans=[plan]), site_asset=site), settings
        )
    )
    ids = {s.id for p in with_site.phases for s in p.steps}
    assert {"confirm_owner", "operator_agreement"} <= ids
    assert [i.id for i in with_site.ideas] == ["site_0"]
    sections = {s.id for s in with_site.sections}
    assert all(x in sections for p in with_site.phases for s in p.steps for x in s.section_ids)
    resolution = next(s for p in with_site.phases for s in p.steps if s.id == "resolution")
    assert resolution.section_ids == ["resolution"]
    # Document ids on steps all resolve to documents in the plan.
    known = {d.id for d in with_site.documents}
    assert all(d in known for p in with_site.phases for s in p.steps for d in s.document_ids)
    assert [s.id for s in with_site.sections] == ["summary", "resolution", "letter", "narrative"]
    assert all(s.generated_by == "template" for s in with_site.sections)  # no Gemini key


def test_steps_count_back_from_the_grant_deadline(settings):
    from datetime import date, timedelta

    from app.models import FundingMatch

    soon = (date.today() + timedelta(days=60)).isoformat()
    later = (date.today() + timedelta(days=200)).isoformat()

    def lead(pid, deadline):
        return FundingMatch(
            program_id=pid,
            name=pid,
            agency="EPA",
            status="open",
            deadline=deadline,
            key_terms="",
            verified_on="2026-11-14",
            source_url=None,
            kind="grant",
            county_eligible="yes",
        )

    opp = opportunity(
        funding=[lead("later", later), lead("soon", soon), lead("past", "2020-01-01")]
    )
    plan = asyncio.run(process.build_process(request(opportunity=opp), settings))
    assert plan.target.program_id == "soon"  # earliest open deadline still ahead
    assert [t.program_id for t in plan.targets] == ["soon", "later"]
    steps = {s.id: s for p in plan.phases for s in p.steps}
    assert steps["submit_application"].due == soon
    # brief_board is due 90 days before a deadline 60 days away: already late.
    assert steps["brief_board"].late is True
    assert steps["procurement"].due is None  # after award

    chosen = asyncio.run(
        process.build_process(request(opportunity=opp, target_program_id="later"), settings)
    )
    steps = {s.id: s for p in chosen.phases for s in p.steps}
    assert chosen.target.program_id == "later" and steps["brief_board"].late is False


def test_drafts_read_like_county_documents(settings):
    site = make_asset("lmop:7", "lmop_landfill")
    plan = asyncio.run(process.build_process(request(site_asset=site), settings))
    by_id = {s.id: s.text for s in plan.sections}
    assert "AGENDA ITEM SUMMARY" in by_id["summary"] and "RECOMMENDED ACTION" in by_id["summary"]
    # Texas: the Commissioners Court, chaired by the County Judge.
    assert "COMMISSIONERS COURT" in by_id["resolution"] and "County Judge" in by_id["resolution"]
    assert "WHEREAS" in by_id["resolution"] and "APPROVED AS TO FORM" in by_id["resolution"]
    assert by_id["letter"].count("Re: ") == 1 and "not an offer" in by_id["letter"]
    assert "2. Statement of need" in by_id["narrative"]
    # Unknowns are blanks, never made up.
    assert "[Owner name]" in by_id["letter"] and "[____]" in by_id["resolution"]


def test_targets_are_grants_a_county_can_apply_for(settings):
    from datetime import date, timedelta

    from app.models import FundingMatch

    soon = (date.today() + timedelta(days=30)).isoformat()
    later = (date.today() + timedelta(days=90)).isoformat()

    def lead(pid, kind, eligible, deadline):
        return FundingMatch(
            program_id=pid,
            name=pid,
            agency="X",
            status="open",
            deadline=deadline,
            key_terms="",
            verified_on="2026-11-14",
            source_url=None,
            kind=kind,
            county_eligible=eligible,
        )

    funding = [
        lead("credit", "tax_credit", "yes", soon),  # a placed-in-service date
        lead("not_counties", "grant", "no", soon),
        lead("unchecked", "grant", "unknown", soon),
        lead("county_ok", "grant", "yes", later),
    ]
    plan = asyncio.run(
        process.build_process(request(opportunity=opportunity(funding=funding)), settings)
    )
    assert [t.program_id for t in plan.targets] == ["county_ok", "unchecked"]
    assert plan.target.program_id == "county_ok"


def test_every_step_says_what_it_rests_on(settings):
    templates = process.load_templates(settings.data_dir / "process_templates.json")
    steps = templates["default"] + [s for g in templates["by_solution"].values() for s in g]
    assert all(s.get("basis") in ("common_practice", "program_rule") for s in steps)
    assert all(s.get("source_url") for s in steps if s["basis"] == "program_rule")
