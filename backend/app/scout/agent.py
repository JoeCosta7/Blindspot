"""The scout's decision loop.

Each turn Gemini reads the county's needs and the state so far and returns
one action (a structured-output call on app.gemini). Code validates it, runs
the tool, and shows the result back. The loop stops at the target number of
shortlisted sites, when the model says finish, or when the action budget is
spent. If Gemini fails twice in a row the rule fallback runs the same tools
in a fixed order, so the run still ends with a result.

Gemini decides which buildings to look at and in what order. Code computes
every measurement, check, estimate, score and funding match (tools.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from app import funding, gemini, matching
from app.config import Settings
from app.geo import County
from app.models import Asset, Need, ScoutEvent, ScoutResult, ScoutStop, SourceStatus
from app.narrative import extract_numbers, format_need_value
from app.scout import drafts, profiles, tools
from app.scout.provider import BuildingData
from app.scout.tools import ScoutState, add_step

log = logging.getLogger(__name__)

SOURCE = "scout_gemini"
THOUGHT_MAX_WORDS = 40


class AgentAction(BaseModel):
    """What the model returns each turn."""

    thought: str
    tool: Literal[
        "search_properties",
        "search_public_buildings",
        "search_report_sites",
        "search_scans",
        "search_imagery",
        "inspect_roof",
        "inspect_interior",
        "evaluate_site",
        "finish",
    ]
    building_id: str | None = None
    # inspect_roof: up to 4 buildings inspected together (faster than one by one).
    building_ids: list[str] = []
    # search_properties: type words such as Industrial, Warehouse, Retail, Office, Flex.
    property_types: list[str] = []
    min_sqft: int | None = None
    max_stories: int | None = None
    # search_public_buildings: school, library, college.
    kinds: list[str] = []
    # search_scans: words to look for in building names and room labels.
    keywords: list[str] = []
    # search_imagery: what to look for from above, in plain words.
    text: str | None = None
    # search_imagery: search around this building instead of the county centre.
    near_building_id: str | None = None
    # evaluate_site: the project types this building could host.
    solution_ids: list[str] = []
    # evaluate_site: why this site, for the official. Numbers only from the state.
    note: str | None = None


Decider = Callable[[str], Awaitable[AgentAction]]

# Kept here so main.py can call scout_agent.profile_for.
profile_for = profiles.profile_for


RULES = (
    "Code measures and scores every site; you choose what to look at and what it could host.",
    "Inspect a building's roof (inspect_roof) before you evaluate it. Buildings with a 3D scan "
    "have their interior read automatically when you evaluate them.",
    "Match the host to the project. The focus below says which kind of building fits and "
    "which search to run first: public buildings (search_public_buildings) when the county "
    "itself would run the project; industrial, flex or retail records (search_properties) "
    "when an existing commercial building is to be reused. Avoid homes and apartments. "
    "search_scans is the fallback, where names and room labels are the only description.",
    "Widen or change a search rather than evaluate a weak candidate. Use search_imagery to find "
    "buildings that have no scan, searching around a promising building.",
    "Write thought in one plain sentence for a county official, at most 40 words, using only "
    "numbers that appear in the state below.",
    "Say finish when the target is reached or nothing more is worth inspecting.",
)


def build_prompt(state: ScoutState) -> str:
    settings = state.settings
    needs = [
        {
            "need_id": n.id,
            "label": n.label,
            "level": n.level,
            "county": format_need_value(n.value, n.unit),
            "us": format_need_value(n.national_value, n.unit),
            "detail": (n.detail or "")[:200] or None,
        }
        for n in sorted(state.needs, key=lambda n: -n.priority)
    ]
    projects = [
        {"project_type": s.id, "name": s.name, "answers": s.triggers} for s in state.solutions
    ]
    buildings = []
    for insp in state.seen.values():
        line: dict[str, Any] = {"building_id": insp.building.id, "name": insp.building.name}
        line["roof"] = "inspected" if insp.roof_checked else "not inspected"
        if insp.building.scan_id:
            line["scan"] = "read" if insp.interior_checked else "available"
        if insp.candidate:
            line["result"] = f"{insp.candidate.status}, score {insp.candidate.score}"
        buildings.append(line)
    budget = {
        "actions_left": settings.scout_max_actions - state.actions_used,
        "roof_inspections_left": settings.scout_max_roofs - state.roofs_used,
        "shortlisted": state.shortlisted(),
        "target": settings.scout_target_sites,
    }
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(RULES, start=1))
    return (
        f"You are scouting buildings in {state.county.name}, {state.county.state} that could host "
        "a project meeting the county's sustainability needs. Three data layers are available "
        "through the tools: commercial property records (search_properties), a 3D scan library "
        "(search_scans, inspect_interior), and aerial property records and imagery "
        "(inspect_roof, search_imagery), plus the county report (needs).\n\n"
        "Tools:\n"
        "- search_properties(property_types, min_sqft?, max_stories?): property records by type "
        "word (Industrial, Warehouse, Retail, Office, Flex, Multifamily), size and storeys.\n"
        "- search_public_buildings(kinds): the county's schools, libraries and colleges.\n"
        "- search_report_sites(): the sites the county report already found for this project, "
        "such as its landfills, brownfields, power plants or dams.\n"
        "- search_scans(keywords): scanned buildings whose name or room labels contain a keyword.\n"
        "- search_imagery(text, near_building_id?): buildings found by describing them, e.g. "
        '"large flat-roofed warehouse with a big parking lot".\n'
        "- inspect_roof(building_ids, up to 4 at once): footprint, roof shape and material, "
        "existing solar, rooftop "
        "equipment, cars and paved area, from the aerial record and live detection.\n"
        "- inspect_interior(building_id): floors, rooms and a summary from the 3D scan.\n"
        "- evaluate_site(building_id, solution_ids, note): code scores the site for those project "
        "types and adds estimates and funding leads.\n"
        "- finish.\n\n"
        f"Rules:\n{rules}\n\n"
        f"Focus: {state.focus_hosts}. Search {state.source_order[0]} first"
        + (f", asking for {', '.join(state.property_types)}" if state.property_types else "")
        + ".\n"
        + (
            "This project goes on a site, not in a building: start with search_report_sites, "
            "then use search_imagery with a plain description to find more sites like them. "
            "inspect_roof reads the site's parcel from the air.\n\n"
            if state.land_mode
            else "\n"
        )
        + f"Needs, highest priority first:\n{json.dumps(needs, ensure_ascii=False)}\n\n"
        f"Project types:\n{json.dumps(projects)}\n\n"
        f"Buildings seen so far:\n{json.dumps(buildings, ensure_ascii=False)}\n\n"
        f"Budget: {json.dumps(budget)}\n\n"
        f"Result of your last action:\n{state.last_result or '(none yet)'}\n\n"
        "Answer with one action."
    )


async def gemini_decider(settings: Settings) -> Decider:
    async def decide(prompt: str) -> AgentAction:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        return await asyncio.wait_for(
            gemini.generate(prompt, AgentAction, settings), settings.gemini_timeout_s
        )

    return decide


# --- validation ------------------------------------------------------------------------


def problem(action: AgentAction, state: ScoutState) -> str | None:
    """Why the action cannot run, or None."""
    if action.tool == "inspect_roof" and action.building_ids:
        if not any(i in state.seen for i in action.building_ids):
            return "inspect_roof needs building_ids from the buildings seen so far"
    elif action.tool in ("inspect_roof", "inspect_interior", "evaluate_site"):
        if not action.building_id or action.building_id not in state.seen:
            return f"{action.tool} needs a building_id from the buildings seen so far"
    if action.tool == "evaluate_site":
        insp = state.seen[action.building_id or ""]
        if not insp.roof_checked:
            return "evaluate_site: inspect the roof first"
        known = {s.id for s in state.solutions}
        own = state.site_solution is not None
        if not own and action.solution_ids and not set(action.solution_ids) & known:
            return "evaluate_site: none of those project types exist"
    if action.tool == "search_imagery" and not (action.text or "").strip():
        return "search_imagery needs text describing what to look for"
    return None


def _clean_thought(action: AgentAction, prompt: str) -> str | None:
    """The model's thought, or None when it is too long or states a number
    that is not in the state it was shown."""
    thought = " ".join(action.thought.split())
    if not thought or len(thought.split()) > THOUGHT_MAX_WORDS * 1.3:
        return None
    if extract_numbers(thought) - extract_numbers(prompt):
        return None
    return thought


def _code_text(action: AgentAction, state: ScoutState) -> str:
    name = state.seen[action.building_id].building.name if action.building_id in state.seen else ""
    return {
        "search_properties": "Searching the property records for "
        + (", ".join(action.property_types[:5]) or "commercial buildings"),
        "search_report_sites": "Starting from the sites the county report found: "
        + state.focus_hosts,
        "search_public_buildings": "Searching the county's public buildings for "
        + (", ".join(action.kinds[:3]) or "schools, libraries and colleges"),
        "search_scans": "Searching the 3D scan library for "
        + (", ".join(action.keywords[:5]) or "likely buildings"),
        "search_imagery": f'Searching the aerial imagery for "{action.text}"',
        "inspect_roof": f"Inspecting the roof of {name}",
        "inspect_interior": f"Reading the 3D scan of {name}",
        "evaluate_site": f"Evaluating {name}",
        "finish": "Finishing the search",
    }[action.tool]


async def run_tool(action: AgentAction, state: ScoutState) -> str:
    limit = state.settings.scout_search_limit
    if action.tool == "search_properties":
        return await tools.search_properties(
            state, action.property_types, action.min_sqft, action.max_stories, limit
        )
    if action.tool == "search_report_sites":
        return await tools.search_report_sites(state, limit)
    if action.tool == "search_public_buildings":
        return await tools.search_public_buildings(state, action.kinds, limit)
    if action.tool == "search_scans":
        return await tools.search_scans(state, action.keywords, limit)
    if action.tool == "search_imagery":
        return await tools.search_imagery(state, action.text or "", action.near_building_id, limit)
    if action.tool == "inspect_roof":
        ids = list(
            dict.fromkeys(
                [*action.building_ids, *([action.building_id] if action.building_id else [])]
            )
        )
        ids = [i for i in ids if i in state.seen][:4]
        results = await asyncio.gather(*(tools.inspect_roof(state, i) for i in ids))
        state.last_result = "\n".join(results)
        return state.last_result
    if action.tool == "inspect_interior":
        return await tools.inspect_interior(state, action.building_id or "")
    if action.tool == "evaluate_site":
        return await tools.evaluate_site(
            state, action.building_id or "", action.solution_ids, action.note
        )
    return "finished"


# --- the rule fallback ------------------------------------------------------------------


def rule_actions(state: ScoutState) -> AgentAction:
    """The next action when the model is unavailable: search the property
    records, then the scans if that found nothing, inspect the roofs in
    order, evaluate each, finish."""
    searched = {s.tool for s in state.steps}
    for source in state.source_order:
        if source in searched:
            continue
        if source == "search_report_sites":
            if state.report_sites:
                return AgentAction(thought="", tool="search_report_sites")
            continue
        if source == "search_imagery":
            near = next(iter(state.seen), None)
            return AgentAction(
                thought="",
                tool="search_imagery",
                text=state.imagery_text or "large open site",
                near_building_id=near,
            )
        if source == "search_public_buildings":
            if state.public_buildings:
                return AgentAction(thought="", tool="search_public_buildings")
            continue
        if source == "search_properties":
            return AgentAction(
                thought="",
                tool="search_properties",
                property_types=list(state.property_types),
                max_stories=2,
            )
        return AgentAction(thought="", tool="search_scans", keywords=list(tools.DEFAULT_KEYWORDS))
    if not state.seen:
        return AgentAction(thought="", tool="finish")
    left = state.settings.scout_max_roofs - state.roofs_used
    pending = [i.building for i in state.seen.values() if not i.roof_checked]
    if not state.land_mode:
        # Building projects: take candidates from each source in turn, so the
        # first source searched cannot fill every inspection slot. Land projects
        # keep their order: the report's own sites come before look-alikes.
        groups: dict[str, list] = {}
        for b in pending:
            groups.setdefault(b.found_by, []).append(b)
        lists = list(groups.values())
        pending = [
            g[i] for i in range(max(map(len, lists), default=0)) for g in lists if i < len(g)
        ]
    todo = [b.id for b in pending][: min(4, left)]
    if todo:
        return AgentAction(thought="", tool="inspect_roof", building_ids=todo)
    for insp in state.seen.values():
        if insp.roof_checked and insp.candidate is None:
            return AgentAction(thought="", tool="evaluate_site", building_id=insp.building.id)
    return AgentAction(thought="", tool="finish")


# --- the loop ---------------------------------------------------------------------------


def _drain(state: ScoutState) -> list[ScoutEvent]:
    events, state.pending = state.pending, []
    return events


async def run_scout(
    county: County,
    needs: list[Need],
    solar_yield: float | None,
    provider: BuildingData,
    settings: Settings,
    client: httpx.AsyncClient,
    decider: Decider | None = None,
    energy_community: dict[str, Any] | None = None,
    energy_community_checked: bool = False,
    focus: str | None = None,
    public_buildings: list[Asset] | None = None,
    profile: profiles.Profile | None = None,
    report_sites: list[Asset] | None = None,
    site_solution: matching.Solution | None = None,
) -> AsyncIterator[ScoutEvent]:
    """Run the agent for one county, yielding steps, candidates and finally
    the result. Never raises past the result event."""
    state = ScoutState(
        county=county,
        needs=needs,
        solar_yield=solar_yield,
        solutions=matching.load_solutions(settings.data_dir / "scout_solutions.json"),
        programs=funding.load_programs(settings.data_dir / "funding_programs.json"),
        provider=provider,
        settings=settings,
        client=client,
        energy_community=energy_community,
        energy_community_checked=energy_community_checked,
        public_buildings=public_buildings or [],
        source_order=(profile or profiles.COMMERCIAL).source_order,
        property_types=list((profile or profiles.COMMERCIAL).property_types),
        prefer_public=(profile or profiles.COMMERCIAL).prefer_public,
        focus_hosts=(profile or profiles.COMMERCIAL).hosts,
        land_mode=bool(profile and profile.land),
        public_kinds=profile.public_kinds if profile else (),
        check_ids=profile.check_ids if profile else (),
        imagery_text=(profile.imagery_text if profile else None),
        report_sites=report_sites or [],
        site_solution=site_solution,
    )
    decide = decider or await gemini_decider(settings)
    generated_by: Literal["gemini", "rules"] = "gemini"
    failures = 0
    stopped: ScoutStop = "budget_spent"

    top = [n.label for n in sorted(needs, key=lambda n: -n.priority)[:3]]
    add_step(
        state,
        "plan",
        (
            f"Scouting {county.name} for places to host: {focus}. "
            if focus
            else f"Scouting {county.name}. "
        )
        + f"Needs in scope: {', '.join(top) or 'none'}. "
        + f"Looking for: {state.focus_hosts}"
        + (
            " (chosen for this project by Gemini)."
            if profile and profile.chosen_by == "gemini"
            else "."
        ),
    )
    for event in _drain(state):
        yield event

    if not needs:
        stopped = "error"
        add_step(
            state,
            "error",
            "No needs were flagged for this county, so there is nothing to scout for.",
        )
        for event in _drain(state):
            yield event
    while needs and state.actions_used < settings.scout_max_actions:
        prompt = build_prompt(state)
        action: AgentAction | None = None
        if generated_by == "gemini":
            try:
                action = await decide(prompt)
                failures = 0
            except Exception as exc:  # SDK, network, timeout or parsing
                failures += 1
                log.warning("scout decider failed (%d): %r", failures, exc)
                if failures >= 2:
                    generated_by = "rules"
                    state.source_status.append(
                        SourceStatus(
                            source=SOURCE,
                            ok=False,
                            cached=False,
                            message=(
                                f"Gemini call failed ({gemini.describe_error(exc)}); "
                                "rule fallback used"
                            ),
                        )
                    )
                    add_step(
                        state, "error", "Gemini is unavailable; continuing with the fixed rules."
                    )
                else:
                    await asyncio.sleep(2)
                    continue
        if action is None:
            action = rule_actions(state)
        state.actions_used += 1

        why = problem(action, state)
        if why:
            add_step(state, "error", f"Skipped an action: {why}.", action.tool)
            state.last_result = f"Invalid action: {why}"
            for event in _drain(state):
                yield event
            continue

        thought = _clean_thought(action, prompt) if generated_by == "gemini" else None
        if action.tool not in ("inspect_roof", "inspect_interior", "evaluate_site"):
            add_step(state, "action", thought or _code_text(action, state), action.tool)
        elif thought and action.building_id in state.seen:
            add_step(
                state, "action", thought, action.tool, state.seen[action.building_id or ""].building
            )
        for event in _drain(state):
            yield event

        if action.tool == "finish":
            stopped = (
                "target_reached"
                if state.shortlisted() >= settings.scout_target_sites
                else "no_more_candidates"
            )
            break
        await run_tool(action, state)
        for event in _drain(state):
            yield event
        # Land projects stop once enough sites pass; building projects compare
        # every inspected candidate and then keep the best.
        if state.land_mode and state.shortlisted() >= settings.scout_target_sites:
            stopped = "target_reached"
            break
        if state.empty_searches >= 3:
            stopped = "no_more_candidates"
            break

    if generated_by == "gemini" and not any(s.source == SOURCE for s in state.source_status):
        state.source_status.append(SourceStatus(source=SOURCE, ok=True, cached=False))

    order = {"shortlisted": 0, "low_confidence": 1, "rejected": 2}
    candidates = sorted(
        (i.candidate for i in state.seen.values() if i.candidate),
        key=lambda c: (order[c.status], -c.score),
    )
    # Photos only for the sites that will be shown: the best non-rejected ones.
    best = [c for c in candidates if c.status != "rejected"][: settings.scout_target_sites]
    if best:
        add_step(state, "action", f"Fetching aerial photos of the best {len(best)} sites")
        for event in _drain(state):
            yield event
        with_photos = {c.building_id: c for c in await tools.fetch_photos(state, best)}
        candidates = [with_photos.get(c.building_id, c) for c in candidates]
    if not candidates and stopped != "error":
        stopped = "no_more_candidates" if state.seen else "error"
    result = ScoutResult(
        fips=county.fips,
        candidates=candidates,
        drafts=[drafts.template_drafts(county, c) for c in candidates if c.status == "shortlisted"][
            : settings.scout_draft_sites
        ],
        steps=state.steps,
        stopped_because=stopped,
        source_status=state.source_status,
        generated_by=generated_by,
        provider="real" if provider.name == "real" else "fake",
    )
    yield ScoutEvent(type="result", result=result)
