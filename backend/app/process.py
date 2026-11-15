"""The government process for one opportunity: an ordered checklist and a
printable document pack.

Steps come from the hand-written data/process_templates.json (general
practice, never statutes), the chosen site's plan, and the opportunity's own
plan. Documents are the curated project documents plus funding pages; nothing
is invented. The pack is filled by code from the opportunity's own figures.
Gemini may reword the summary and the grant narrative, and its text is
rejected if it carries a number the template did not (narrative.extract_numbers).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app import documents, gemini
from app.cache import DAY, cache_get, cache_set
from app.config import Settings
from app.estimates import format_number
from app.models import (
    FundingMatch,
    PackSection,
    ProcessPhase,
    ProcessPlan,
    ProcessRequest,
    ProcessStep,
    ProjectDocument,
)
from app.narrative import extract_numbers

log = logging.getLogger(__name__)

CAVEATS = [
    "These are typical steps for a county project, not legal advice. Confirm the process "
    "with the county attorney and with each funding agency.",
    "Funding leads were checked on the dates shown; programs change. Confirm status and "
    "eligibility before applying.",
    "Capacity, energy and household figures are estimates with the assumptions shown in "
    "the report.",
]


@lru_cache(maxsize=2)
def load_templates(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _template_steps(
    templates: dict[str, Any], solution_id: str | None, has_site: bool, known_docs: set[str]
) -> dict[str, list[ProcessStep]]:
    """Template steps by phase: the defaults, then the project type's own,
    a project type's step replacing a default with the same id."""
    by_id: dict[str, dict[str, Any]] = {s["id"]: s for s in templates["default"]}
    for step in templates["by_solution"].get(solution_id or "", []):
        by_id[step["id"]] = step
    out: dict[str, list[ProcessStep]] = {}
    for raw in by_id.values():
        if raw.get("site_only") and not has_site:
            continue
        out.setdefault(raw["phase"], []).append(
            ProcessStep(
                id=raw["id"],
                title=raw["title"],
                detail=raw.get("detail"),
                who=raw.get("who"),
                document_ids=[d for d in raw.get("document_ids", []) if d in known_docs],
                section_ids=[x for x in raw.get("section_ids", []) if x in SECTION_IDS],
                due=str(raw["lead_days"]) if raw.get("lead_days") is not None else None,
                source="template",
            )
        )
    return out


def _site_plan(request: ProcessRequest):
    site_id = (
        request.site_asset.id
        if request.site_asset
        else (request.site.building_id if request.site else None)
    )
    for plan in request.opportunity.site_plans:
        if plan.site_id == site_id:
            return plan
    return None


SECTION_IDS = ("summary", "resolution", "letter", "narrative")


def build_phases(
    request: ProcessRequest, templates: dict[str, Any], known_docs: set[str]
) -> tuple[list[ProcessPhase], list[ProcessStep]]:
    """The checklist phases, and the site's or opportunity's own plan lines
    kept apart as ideas."""
    has_site = request.site is not None or request.site_asset is not None
    steps = _template_steps(templates, request.opportunity.solution_id, has_site, known_docs)
    phases = [
        ProcessPhase(id=p["id"], title=p["title"], steps=steps.get(p["id"], []))
        for p in templates["phases"]
    ]
    site_plan = _site_plan(request)
    extra: list[ProcessStep] = []
    if site_plan is not None:
        extra += [
            ProcessStep(id=f"site_{i}", title=t, source="site_plan")
            for i, t in enumerate(site_plan.course_of_action)
        ]
        extra += [
            ProcessStep(
                id=f"site_check_{i}",
                title=t,
                detail="Check with the agency named.",
                source="site_plan",
            )
            for i, t in enumerate(site_plan.compliance)
        ]
    elif request.opportunity.plan:
        extra += [
            ProcessStep(id=f"plan_{i}", title=t, source="plan")
            for i, t in enumerate(request.opportunity.plan)
        ]
    return [p for p in phases if p.steps], extra


def gather_documents(request: ProcessRequest, data_dir: Path) -> list[ProjectDocument]:
    opp = request.opportunity
    asset_type = request.site_asset.type if request.site_asset else None
    matched = documents.match_documents(
        documents.load_documents(data_dir / "project_documents.json"), opp.solution_id, asset_type
    )
    return documents.merge_documents(
        list(opp.documents), matched, documents.documents_from_funding(opp.funding)
    )


# --- the pack ----------------------------------------------------------------------------


def _estimates_line(request: ProcessRequest) -> str:
    figures = request.site.estimates if request.site else request.opportunity.estimates
    if not figures:
        return ""
    parts = [f"{e.label.lower()} about {format_number(e.value)} {e.unit}" for e in figures]
    return "Planning estimates: " + "; ".join(parts) + "."


def template_sections(request: ProcessRequest) -> list[PackSection]:
    opp = request.opportunity
    place = f"{request.county_name}, {request.state}"
    needs = [n.label.lower() for n in request.needs] or ["the needs named in the county report"]
    need_text = ", ".join(needs)
    estimates = _estimates_line(request)

    if request.site:
        s = request.site
        site_text = f"{s.name}" + (f", {s.address}" if s.address else "") + ". "
        site_text += f"Status from Site Scout: {s.status.replace('_', ' ')} ({s.status_reason}). "
        facts = [
            f"{o.label}: {o.value}{' ' + o.unit if o.unit else ''}" for o in s.observations[:6]
        ]
        site_text += ("Facts from the data layers: " + "; ".join(facts) + ".") if facts else ""
    elif request.site_asset:
        a = request.site_asset
        site_text = f"{a.name} ({a.type.replace('_', ' ')}), from {a.source}."
    else:
        site_text = "This is a county-wide program; it does not depend on a single site."

    lead_lines = [
        f"- {f.name} ({f.agency}): {f.status.replace('_', ' ')}"
        + (f", deadline {f.deadline}" if f.deadline else "")
        + (f", checked {f.verified_on}" if f.verified_on else "")
        for f in opp.funding
    ]
    funding_text = (
        "Funding leads to confirm with each agency:\n" + "\n".join(lead_lines)
        if lead_lines
        else "No funding lead is matched yet; the grants coordinator should search state "
        "and federal programs."
    )
    program_names = [f.name for f in opp.funding if f.status in ("open", "open_with_deadlines")][:3]

    lead = (
        f"{place} proposes: {opp.title}. The project answers {need_text}. "
        + (opp.summary + " " if opp.summary else "")
        + estimates
    ).strip()
    # One summary: the project, then the site, then the funding leads. Only the
    # first paragraph is ever reworded (reword).
    summary = f"{lead}\n\nSite: {site_text}\n\n{funding_text}"
    resolution = (
        "RESOLUTION NO. ____ (draft for the county attorney's review)\n\n"
        f"A resolution of the governing board of {request.county_name} authorizing staff to "
        f"pursue {opp.title.lower()}.\n\n"
        f"WHEREAS the county faces {need_text}; and\n"
        f"WHEREAS {opp.title.lower()} could reduce these needs"
        + (
            " at " + (request.site.name if request.site else request.site_asset.name)
            if (request.site or request.site_asset)
            else ""
        )
        + "; and\n"
        "WHEREAS outside funding may be available to pay for it;\n\n"
        "NOW, THEREFORE, BE IT RESOLVED that the board authorizes the county administrator to "
        "develop the project, engage partners and the public, and prepare applications"
        + (f" to programs including {', '.join(program_names)}" if program_names else "")
        + ", subject to the board's later approval of any agreement or expenditure."
    )
    partner = (
        request.site.name
        if request.site
        else (request.site_asset.name if request.site_asset else "our community partners")
    )
    letter = (
        f"To the owner or manager of {partner},\n\n"
        f"{request.county_name} is exploring {opp.title.lower()} to address {need_text}. "
        "We would like to talk with you about whether a partnership could work. "
        + (estimates + " " if estimates else "")
        + "This letter is an invitation to a conversation, not a proposal or a commitment.\n\n"
        f"Sincerely,\n{request.county_name}"
    )
    narrative = (
        f"{request.county_name} will carry out {opp.title.lower()}"
        + (f" at {partner}" if request.site or request.site_asset else "")
        + f" to address {need_text}. "
        + (opp.rationale + " " if opp.rationale else "")
        + (estimates + " " if estimates else "")
        + "Figures are preliminary estimates; the county will confirm eligibility with each "
        "program before applying."
    ).strip()
    rows = [
        ("summary", "Project summary", summary),
        ("resolution", "Draft board resolution", resolution),
        ("letter", "Partner or owner letter", letter),
        ("narrative", "Grant narrative paragraph", narrative),
    ]
    return [PackSection(id=i, title=t, text=x, generated_by="template") for i, t, x in rows]


class _Reworded(BaseModel):
    summary: str
    narrative: str


REWORD_PROMPT = (
    "Rewrite these two passages for a county grant packet, in plain, confident language for "
    "a non-technical reader. Keep every fact. Do not add numbers, dates, names, programs or "
    "promises that are not in the text. Keep each under 120 words.\n\n"
    "summary:\n{summary}\n\nnarrative:\n{narrative}"
)


async def reword(sections: list[PackSection], settings: Settings) -> list[PackSection]:
    """Gemini rewords the summary and narrative; anything with a new number is
    rejected and the template kept. Never raises."""
    if not settings.gemini_api_key:
        return sections
    by_id = {s.id: s for s in sections}
    # The summary's first paragraph only; the site and funding lines stay as written.
    head, _, tail = by_id["summary"].text.partition("\n\n")
    prompt = REWORD_PROMPT.format(summary=head, narrative=by_id["narrative"].text)
    key = {"model": settings.gemini_model, "prompt": hashlib.sha256(prompt.encode()).hexdigest()}
    hit = cache_get(settings.cache_dir, "process_reword", key, 30 * DAY)
    try:
        result = (
            _Reworded.model_validate(hit)
            if hit
            else await asyncio.wait_for(
                gemini.generate(prompt, _Reworded, settings), settings.gemini_timeout_s
            )
        )
    except Exception as exc:  # SDK, network, timeout or parsing
        log.warning("process reword failed: %r", exc)
        return sections
    if not hit:
        cache_set(settings.cache_dir, "process_reword", key, result.model_dump())
    out = []
    for s in sections:
        new = getattr(result, s.id, None) if s.id in ("summary", "narrative") else None
        original = head if s.id == "summary" else s.text
        if new and new.strip() and extract_numbers(new) <= extract_numbers(original):
            text = new.strip() + ("\n\n" + tail if s.id == "summary" and tail else "")
            out.append(s.model_copy(update={"text": text, "generated_by": "gemini"}))
        else:
            out.append(s)
    return out


def dated_targets(funding: list[FundingMatch], today: date) -> list[FundingMatch]:
    """Leads one can aim for: open, with a deadline still ahead. Soonest first."""
    leads = [
        f
        for f in funding
        if f.deadline
        and f.status in ("open", "open_with_deadlines")
        and f.deadline >= today.isoformat()
    ]
    return sorted(leads, key=lambda f: f.deadline or "")


def schedule(
    phases: list[ProcessPhase], target: FundingMatch | None, today: date
) -> list[ProcessPhase]:
    """Turn each step's lead days (carried in `due` by _template_steps) into a
    do-by date before the target's deadline, and add the submission itself."""
    deadline = date.fromisoformat(target.deadline) if target and target.deadline else None
    out = []
    for phase in phases:
        steps = []
        for step in phase.steps:
            lead = int(step.due) if step.due and step.due.isdigit() else None
            if deadline is None or lead is None:
                steps.append(step.model_copy(update={"due": None, "late": False}))
                continue
            due = deadline - timedelta(days=lead)
            steps.append(step.model_copy(update={"due": due.isoformat(), "late": due < today}))
        if phase.id == "fund" and target is not None and deadline is not None:
            steps.append(
                ProcessStep(
                    id="submit_application",
                    title=f"Submit the application to {target.name}",
                    detail=f"{target.agency} deadline. Confirm the date with the agency.",
                    who="Grants coordinator",
                    section_ids=["narrative"],
                    due=deadline.isoformat(),
                    late=False,
                    source="template",
                )
            )
        out.append(phase.model_copy(update={"steps": steps}))
    return out


async def build_process(request: ProcessRequest, settings: Settings) -> ProcessPlan:
    templates = load_templates(settings.data_dir / "process_templates.json")
    docs = gather_documents(request, settings.data_dir)
    phases, ideas = build_phases(request, templates, {d.id for d in docs})
    today = date.today()
    targets = dated_targets(request.opportunity.funding, today)
    target = next(
        (f for f in targets if f.program_id == request.target_program_id),
        targets[0] if targets else None,
    )
    phases = schedule(phases, target, today)
    plan = _site_plan(request)
    needed = (
        plan.documents_needed
        if plan and plan.documents_needed
        else list(request.opportunity.documents_needed)
    )
    sections = await reword(template_sections(request), settings)
    site_id = (
        request.site.building_id
        if request.site
        else (request.site_asset.id if request.site_asset else None)
    )
    return ProcessPlan(
        opportunity_id=request.opportunity.id,
        site_id=site_id,
        target=target,
        targets=targets,
        today=today.isoformat(),
        phases=phases,
        ideas=ideas,
        documents=docs,
        documents_needed=needed,
        sections=sections,
        caveats=CAVEATS,
    )
