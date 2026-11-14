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
    assert any(
        p.id == "specific" and p.title == "The project's own plan" for p in county_wide.phases
    )

    site = make_asset("lmop:7", "lmop_landfill")
    plan = SiteActionPlan(site_id="lmop:7", course_of_action=["Walk the cell with the operator"])
    with_site = asyncio.run(
        process.build_process(
            request(opportunity=opportunity(site_plans=[plan]), site_asset=site), settings
        )
    )
    ids = {s.id for p in with_site.phases for s in p.steps}
    assert {"confirm_owner", "operator_agreement", "site_0"} <= ids
    # Document ids on steps all resolve to documents in the plan.
    known = {d.id for d in with_site.documents}
    assert all(d in known for p in with_site.phases for s in p.steps for d in s.document_ids)
    assert [s.id for s in with_site.sections] == [
        "summary",
        "site",
        "funding",
        "resolution",
        "letter",
        "narrative",
    ]
    assert all(s.generated_by == "template" for s in with_site.sections)  # no Gemini key


def test_reword_rejects_new_numbers(settings, monkeypatch):
    sections = process.template_sections(request())

    async def fake(prompt, schema, s):
        return schema(summary="It will serve 40,000 homes.", narrative="Plainly worded narrative.")

    monkeypatch.setattr(process.gemini, "generate", fake)
    out = asyncio.run(process.reword(sections, settings.model_copy(update={"gemini_api_key": "x"})))
    by_id = {s.id: s for s in out}
    assert by_id["summary"].generated_by == "template"  # 40,000 was invented
    assert by_id["narrative"].generated_by == "gemini"
