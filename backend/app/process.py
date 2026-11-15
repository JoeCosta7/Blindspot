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

import json
import logging
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import documents, packet
from app.config import Settings
from app.models import (
    FundingMatch,
    ProcessPhase,
    ProcessPlan,
    ProcessRequest,
    ProcessStep,
    ProjectDocument,
)

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
    sections = packet.build_sections(request, target, phases, today)
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
