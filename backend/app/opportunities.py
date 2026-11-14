"""Opportunities written by Gemini.

The system has three parts. Needs say what is wrong in a county (needs.py).
Factors and sites say what the county has to work with (factors.py and the
mapped assets). Here Gemini reads both and writes the opportunities: projects
and programs that use what the county has to meet its needs.

Three steps:

1. `build_digest` puts the needs, the factors and a few sites of each kind
   into one compact input. The model never sees the full asset list.
2. `write_opportunities` sends the digest and checks every opportunity that
   comes back against it. One is dropped if it names a need or site that is
   not in the digest, draws on no factor or site, or contains a number that is
   not in the digest. The same call returns the county summary.
3. The pipeline turns each surviving draft into an Opportunity and adds what
   only code may supply: the score, the estimates and the funding leads.

Gemini chooses and words the solutions. It supplies no numbers, no rankings
and no funding programs. With no key, an error, a timeout or nothing usable,
the pipeline falls back to the rule matches from matching.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app import gemini
from app.cache import DAY, cache_get, cache_set
from app.config import Settings
from app.estimates import format_number, round_sig
from app.matching import Solution
from app.models import Asset, Factor, Highlight, Need, SourceStatus
from app.narrative import extract_numbers, format_need_value

log = logging.getLogger(__name__)

SOURCE = "gemini"

# Lengths asked for in the prompt. Models overshoot word counts a little, so
# text is only rejected past the limit times LENGTH_SLACK.
TITLE_MAX_WORDS = 12
TEXT_MAX_WORDS = 50
STEP_MAX_WORDS = 25
MAX_STEPS = 6
MAX_COMPLIANCE = 4
MAX_DOCUMENTS = 5
SUMMARY_MAX_WORDS = 90
LENGTH_SLACK = 1.3

# What the model answers when its idea is none of the catalogued project types.
OTHER_PROJECT_TYPE = "other"

ASSET_LABELS = {
    "brownfield": "Brownfields",
    "contaminated_site": "Cleanup sites (Superfund, RCRA or state)",
    "landfill": "Landfills (EPA RE-Powering list)",
    "mine_land": "Abandoned mine lands",
    "lmop_landfill": "Landfills (EPA landfill gas database)",
    "school": "Schools",
    "library": "Libraries",
    "college": "Colleges",
    "ev_station": "Public EV charging stations",
    "non_powered_dam": "Dams without hydropower",
    "power_plant": "Power plants (1 MW or more)",
}
# Counted but never listed: nobody builds a project at an existing charger.
COUNT_ONLY_TYPES = {"ev_station"}
# Keys whose values are identifiers, not prose: their digits are not figures.
ID_KEYS = {"site_id", "factor_id", "need_id", "project_type"}

PROMPT_RULES = (
    "Write for a county official with no technical background.",
    'Every opportunity must directly reduce at least one need listed under "needs". Put '
    "only the need_id values it directly reduces in need_ids, exactly as given.",
    "Every opportunity must draw on what the county has: at least one factor or one site. "
    "Put the factor_id values it relies on in factor_ids and the site_id values of the "
    "places it would use in site_ids, exactly as given. Leave site_ids empty only for a "
    "county-wide program.",
    "Variety first. Do not give two opportunities the same project_type. Community solar, "
    "wind on reclaimed land and public EV charging are allowed at most once each across "
    "the whole list. Prefer a mix of catalogued project_types and "
    f'"{OTHER_PROJECT_TYPE}" ideas that fit this county.',
    'Lead with what makes this county unusual. The "standouts" list is what it has more '
    "of than a typical US county. At least half of the opportunities must draw on a "
    "standout factor or on a kind of site that is scarce in most counties (a dam, "
    "landfill gas candidate, power plant, large livestock farms, or a named school, "
    "library or college).",
    "Cover different needs. An opportunity's top need is the highest-priority need_id it "
    "lists. Do not give more than two opportunities the same top need. Secondary needs "
    "may repeat. Answer high-priority needs, but spread across the list rather than "
    "stacking every opportunity on the first need.",
    "Fit each opportunity to the county. Where its weather, terrain, land cover, "
    "workforce, industries or community change what the project should be, who it "
    "should reach first or who would build it, shape the project around that, say so "
    "in why_here and list those factors in factor_ids. Do not list a factor that "
    "changes nothing about the project.",
    "State nothing about the county or a site that the input does not say.",
    "Use only numbers that appear in the input, copied exactly. Do not calculate, "
    "round or estimate: no costs, savings, sizes, percentages, dates or time frames "
    "of your own.",
    "Do not name funding programs, grants or tax credits, and do not promise results. "
    "Code will attach funding leads separately.",
    "Write plan as a start-to-finish course of action a county official can follow "
    f"(about {MAX_STEPS} steps). Write compliance as checks with the right owner or "
    "agency that follow from facts in the input (owner type, cleanup status, candidate "
    "status, federal vs local dam, and so on). Do not invent statute numbers, ordinance "
    "codes or agency rule citations.",
    "For every site_id you list, add a matching entry in site_plans with that site_id, "
    "a course_of_action for that site, compliance checks specific to it, and "
    "documents_needed (forms and agreements to gather for that site, names only). For a "
    "county-wide program with no sites, leave site_plans empty and put the full plan "
    "in plan and documents_needed.",
    "documents_needed are short names of forms or packets to gather: grant applications, "
    "property or lease agreements, interconnection requests, environmental review forms, "
    "and the like. Do not invent URLs, form numbers or PDF links; code attaches real "
    "links separately when it has them.",
    'Do not rely on a kind of site listed under "not_found".',
    'If the opportunity is one of the kinds under "project_types", built at a listed '
    "site of a kind that entry names, give that project_type exactly as written. "
    f'Otherwise give "{OTHER_PROJECT_TYPE}".',
)


class _ModelSitePlan(BaseModel):
    site_id: str
    course_of_action: list[str]
    compliance: list[str] = []
    documents_needed: list[str] = []


class _ModelOpportunity(BaseModel):
    title: str
    need_ids: list[str]
    factor_ids: list[str]
    site_ids: list[str]
    project_type: str
    what: str
    why_here: str
    # County-wide start-to-finish plan. Also accepted as first_steps from older
    # cached Gemini responses.
    plan: list[str] = []
    first_steps: list[str] = []
    compliance: list[str] = []
    documents_needed: list[str] = []
    site_plans: list[_ModelSitePlan] = []


class ModelPlan(BaseModel):
    """The response schema given to Gemini."""

    summary: str
    opportunities: list[_ModelOpportunity]


@dataclass(frozen=True)
class SiteDraft:
    site_id: str
    course_of_action: list[str]
    compliance: list[str]
    documents_needed: list[str]


@dataclass(frozen=True)
class Draft:
    """One opportunity as Gemini wrote it, checked against the digest. The
    pipeline adds the score, estimates, funding and document links."""

    title: str
    summary: str
    rationale: str
    plan: list[str]
    compliance: list[str]
    documents_needed: list[str]
    site_plans: list[SiteDraft]
    need_ids: list[str]
    factor_ids: list[str]
    asset_ids: list[str]
    # A project type from data/solutions.json, or None for an idea of its own.
    solution_id: str | None

    @property
    def first_steps(self) -> list[str]:
        return self.plan


@dataclass(frozen=True)
class Plan:
    # None when the model's summary was unusable; the template is used instead.
    summary: str | None
    drafts: list[Draft]


# --- digest --------------------------------------------------------------------


def _number(value: Any) -> float | None:
    """A finite number, or None. Parquet rows carry NaN for missing values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _amount(value: Any, unit: str) -> str | None:
    number = _number(value)
    return f"{format_number(round_sig(number))} {unit}" if number else None


def _site_facts(asset: Asset) -> str | None:
    """The few attributes that decide what a site could host, as one line."""
    a = asset.attributes
    facts: list[str | None] = []
    if asset.type == "lmop_landfill":
        if a.get("landfill_status"):
            facts.append(f"status: {a['landfill_status']}")
        if a.get("has_operational_project"):
            facts.append("has an operating gas energy project")
        elif a.get("is_candidate"):
            facts.append("EPA candidate for a gas energy project")
        facts.append(_amount(a.get("waste_in_place_tons"), "tons of waste in place"))
        if a.get("ownership_type"):
            facts.append(f"ownership: {a['ownership_type']}")
    elif asset.type == "non_powered_dam":
        capacity = _amount(a.get("capacity_mw"), "MW")
        facts.append(f"ORNL modelled hydropower potential {capacity}" if capacity else None)
        if a.get("waterway"):
            facts.append(f"on {a['waterway']}")
        if a.get("owner_type"):
            facts.append(f"owner type: {a['owner_type']}")
    elif asset.type == "power_plant":
        if a.get("technology"):
            facts.append(str(a["technology"]))
        facts.append(_amount(a.get("nameplate_mw"), "MW operating"))
        retiring = _amount(a.get("planned_retirement_mw"), "MW of fossil units")
        if retiring and a.get("planned_retirement_year"):
            facts.append(f"{retiring} planned to retire from {a['planned_retirement_year']}")
        retired = _amount(a.get("retired_fossil_mw"), "MW of fossil units")
        if retired and a.get("last_retirement_year"):
            facts.append(f"{retired} retired, most recently in {a['last_retirement_year']}")
        voltage = _amount(a.get("grid_voltage_kv"), "kV grid connection")
        facts.append(voltage)
        if a.get("utility_name"):
            facts.append(f"operator: {a['utility_name']}")
    else:
        facts.append(_amount(a.get("acres"), "acres"))
        solar = _amount(a.get("epa_pv_mw"), "MW")
        facts.append(f"EPA solar screening estimate {solar}" if solar else None)
        wind = _amount(a.get("epa_wind_mw"), "MW")
        facts.append(f"EPA wind screening estimate {wind}" if wind else None)
        miles = _number(a.get("transmission_miles"))
        if miles is not None:
            unit = "mile" if miles == 1 else "miles"
            facts.append(f"{format_number(miles)} {unit} to the nearest transmission line")
        if a.get("city"):
            facts.append(f"in {a['city']}")
    line = "; ".join(fact for fact in facts if fact)
    return line or None


def _compact(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if value not in (None, "", [])}


def build_digest(
    county_name: str,
    state: str,
    needs: list[Need],
    factors: list[Factor],
    assets: list[Asset],
    missing_types: list[str],
    solutions: tuple[Solution, ...] | list[Solution],
    sites_per_type: int,
    standouts: list[Highlight] | None = None,
) -> dict[str, Any]:
    """What the model may reason from, and nothing else. `assets` is every
    asset found, best first within its type; only `sites_per_type` of each
    type are listed, with the exact count beside them. `standouts` are the
    factors that beat a typical US county, already computed by highlights.py."""
    by_type: dict[str, list[Asset]] = {}
    for asset in assets:
        by_type.setdefault(asset.type, []).append(asset)

    sites = []
    for asset_type, label in ASSET_LABELS.items():
        found = by_type.get(asset_type, [])
        if not found:
            continue
        listed = [] if asset_type in COUNT_ONLY_TYPES else found[:sites_per_type]
        sites.append(
            _compact(
                {
                    "kind": label,
                    "count": len(found),
                    "examples": [
                        _compact({"site_id": a.id, "name": a.name, "facts": _site_facts(a)})
                        for a in listed
                    ],
                }
            )
        )

    return {
        "county": f"{county_name}, {state}",
        "needs": [
            _compact(
                {
                    "need_id": n.id,
                    "label": n.label,
                    "theme": n.theme,
                    "level": n.level,
                    "county_value": format_need_value(n.value, n.unit),
                    "national_value": format_need_value(n.national_value, n.unit),
                    "detail": n.detail,
                }
            )
            for n in sorted(needs, key=lambda n: (-n.priority, -n.severity))
        ],
        "standouts": [
            _compact(
                {
                    "factor_id": h.factor_id,
                    "label": h.label,
                    "value": h.value,
                    "compared_to": h.benchmark,
                    "times_above_typical": h.times_better,
                    "why_it_matters": h.why,
                }
            )
            for h in standouts or []
        ],
        "factors": [
            _compact(
                {
                    "factor_id": f.id,
                    "kind": f.category,
                    "label": f.label,
                    "value": f.value,
                    "comparison": f.comparison,
                    "note": f.detail,
                }
            )
            for f in factors
        ],
        "sites": sites,
        "not_found": [ASSET_LABELS.get(t, t) for t in missing_types],
        "project_types": [
            _compact(
                {
                    "project_type": s.id,
                    "name": s.name,
                    "built_at": [ASSET_LABELS.get(host, host) for host in s.hosts],
                }
            )
            for s in solutions
        ],
    }


def _site_ids(digest: dict[str, Any]) -> set[str]:
    return {
        example["site_id"] for group in digest["sites"] for example in group.get("examples", [])
    }


def _prose(value: Any) -> Iterator[str]:
    """Every value in the digest except identifiers, whose digits are not figures."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key not in ID_KEYS:
                yield from _prose(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _prose(inner)
    elif value is not None:
        yield str(value)


def allowed_numbers(digest: dict[str, Any]) -> set[float]:
    return extract_numbers(" ".join(_prose(digest)))


# --- validation ------------------------------------------------------------------


def _over(text: str, limit: int) -> bool:
    return len(text.split()) > limit * LENGTH_SLACK


def _invented(text: str, digest: dict[str, Any]) -> str | None:
    invented = sorted(extract_numbers(text) - allowed_numbers(digest))
    if not invented:
        return None
    return ", ".join(format_number(n) for n in invented[:5])


def _plan_steps(item: _ModelOpportunity) -> list[str]:
    """Prefer `plan`; fall back to `first_steps` so older cached answers still work."""
    steps = [step for step in item.plan if step.strip()]
    if steps:
        return steps
    return [step for step in item.first_steps if step.strip()]


def opportunity_problem(item: _ModelOpportunity, digest: dict[str, Any]) -> str | None:
    """Why one opportunity must be dropped, or None if it is usable."""
    if not item.title.strip() or not item.what.strip() or not item.why_here.strip():
        return "a required field is empty"
    known_needs = {n["need_id"] for n in digest["needs"]}
    if not item.need_ids:
        return "it addresses no need"
    if not set(item.need_ids) <= known_needs:
        return "it names a need that is not in the input"
    known_sites = _site_ids(digest)
    if not set(item.site_ids) <= known_sites:
        return "it names a site that is not in the input"
    known_factors = {f["factor_id"] for f in digest["factors"]}
    if not item.site_ids and not set(item.factor_ids) & known_factors:
        return "it draws on none of the county's factors or sites"
    steps = _plan_steps(item)
    if not steps:
        return "it has no plan"
    site_plan_ids = [plan.site_id.strip() for plan in item.site_plans if plan.site_id.strip()]
    if not set(site_plan_ids) <= known_sites:
        return "a site plan names a site that is not in the input"
    if any(
        plan.site_id.strip() and not [s for s in plan.course_of_action if s.strip()]
        for plan in item.site_plans
    ):
        return "a site plan has no course of action"
    compliance = [line for line in item.compliance if line.strip()]
    site_compliance = [line for plan in item.site_plans for line in plan.compliance if line.strip()]
    site_steps = [
        step for plan in item.site_plans for step in plan.course_of_action if step.strip()
    ]
    documents = [line for line in item.documents_needed if line.strip()]
    site_documents = [
        line for plan in item.site_plans for line in plan.documents_needed if line.strip()
    ]
    if (
        _over(item.title, TITLE_MAX_WORDS)
        or _over(item.what, TEXT_MAX_WORDS)
        or _over(item.why_here, TEXT_MAX_WORDS)
        or any(_over(step, STEP_MAX_WORDS) for step in steps)
        or any(_over(step, STEP_MAX_WORDS) for step in site_steps)
        or any(_over(line, STEP_MAX_WORDS) for line in compliance + site_compliance)
        or any(_over(line, STEP_MAX_WORDS) for line in documents + site_documents)
    ):
        return "it is too long"
    invented = _invented(
        " ".join(
            [
                item.title,
                item.what,
                item.why_here,
                *steps,
                *compliance,
                *documents,
                *site_steps,
                *site_compliance,
                *site_documents,
            ]
        ),
        digest,
    )
    if invented:
        return f"it contains numbers that are not in the input: {invented}"
    return None


def summary_problem(summary: str, digest: dict[str, Any]) -> str | None:
    """Why the county summary cannot be used, or None if it can."""
    if not summary.strip():
        return "it is empty"
    if _over(summary, SUMMARY_MAX_WORDS):
        return "it is too long"
    invented = _invented(summary, digest)
    if invented:
        return f"it contains numbers that are not in the input: {invented}"
    return None


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _primary_need(need_ids: list[str], digest: dict[str, Any]) -> str | None:
    """The highest-priority need the opportunity answers. Digest needs are
    already ordered highest first."""
    order = {need["need_id"]: index for index, need in enumerate(digest["needs"])}
    ranked = [need_id for need_id in _unique(need_ids) if need_id in order]
    return min(ranked, key=order.__getitem__) if ranked else None


def keep_valid(
    model: ModelPlan, digest: dict[str, Any], limit: int
) -> tuple[list[Draft], list[str]]:
    """The usable opportunities, at most `limit`, and why each other one was dropped.

    Variety is enforced here as well as in the prompt: a second copy of the
    same catalogued project_type is dropped, and a third opportunity whose
    top need is the same as two already kept is dropped, so solar and EV
    cannot fill the list."""
    known_factors = {f["factor_id"] for f in digest["factors"]}
    project_types = {p["project_type"] for p in digest["project_types"]}
    kept: list[Draft] = []
    dropped: list[str] = []
    titles: set[str] = set()
    used_types: set[str] = set()
    primary_uses: dict[str, int] = {}
    for item in model.opportunities:
        title = item.title.strip()
        problem = opportunity_problem(item, digest)
        project_type = item.project_type.strip()
        catalogued = project_type if project_type in project_types else None
        primary = _primary_need(item.need_ids, digest)
        if problem is None and title.lower() in titles:
            problem = "it repeats another opportunity"
        if problem is None and catalogued is not None and catalogued in used_types:
            problem = "it repeats a project type already kept"
        if problem is None and primary is not None and primary_uses.get(primary, 0) >= 2:
            problem = "it spends a third opportunity on the same top need"
        if problem:
            dropped.append(problem)
            continue
        if len(kept) >= limit:
            continue  # more than asked for; not a fault
        titles.add(title.lower())
        if catalogued is not None:
            used_types.add(catalogued)
        if primary is not None:
            primary_uses[primary] = primary_uses.get(primary, 0) + 1
        named_sites = _unique(item.site_ids)
        plan_steps = _plan_steps(item)[:MAX_STEPS]
        county_compliance = [s.strip() for s in item.compliance if s.strip()][:MAX_COMPLIANCE]
        county_documents = [s.strip() for s in item.documents_needed if s.strip()][:MAX_DOCUMENTS]
        site_plans = [
            SiteDraft(
                site_id=plan.site_id.strip(),
                course_of_action=[s.strip() for s in plan.course_of_action if s.strip()][
                    :MAX_STEPS
                ],
                compliance=[s.strip() for s in plan.compliance if s.strip()][:MAX_COMPLIANCE],
                documents_needed=[s.strip() for s in plan.documents_needed if s.strip()][
                    :MAX_DOCUMENTS
                ],
            )
            for plan in item.site_plans
            if plan.site_id.strip() in set(named_sites)
        ]
        # If Gemini named sites but skipped site_plans, reuse the county plan
        # so the UI still has a course of action per site.
        covered = {plan.site_id for plan in site_plans}
        for site_id in named_sites:
            if site_id not in covered:
                site_plans.append(
                    SiteDraft(
                        site_id=site_id,
                        course_of_action=list(plan_steps),
                        compliance=list(county_compliance),
                        documents_needed=list(county_documents),
                    )
                )
        kept.append(
            Draft(
                title=title,
                summary=item.what.strip(),
                rationale=item.why_here.strip(),
                plan=plan_steps,
                compliance=county_compliance,
                documents_needed=county_documents,
                site_plans=site_plans,
                need_ids=_unique(item.need_ids),
                # A factor id the input does not have is a slip, not a false claim.
                factor_ids=[f for f in _unique(item.factor_ids) if f in known_factors],
                asset_ids=_unique(item.site_ids),
                solution_id=catalogued,
            )
        )
    return kept, dropped


# --- Gemini ----------------------------------------------------------------------


def build_prompt(digest: dict[str, Any], limit: int) -> str:
    rules = "\n".join(f"{i}. {rule}" for i, rule in enumerate(PROMPT_RULES, start=1))
    return (
        "You are advising a US county government on sustainability. The county data below "
        "has four parts:\n"
        "- needs: the county's environmental and socioeconomic problems, measured from "
        "public data.\n"
        "- standouts: the few things this county has more of than a typical US county, "
        "with why each one matters for local projects. Start here.\n"
        "- factors: what the county has to work with: its climate and weather, energy "
        "system, land cover and terrain, buildings, workforce and industries, economy, "
        "community, infrastructure and farms. A factor is not good or bad in itself. A "
        "weakness in one place can be the raw material for a fix in another.\n"
        "- sites: specific places in the county, with what is known about each.\n\n"
        f"Create up to {limit} opportunities. An opportunity is a concrete project or "
        "program that uses the county's factors and sites to meet its needs. The list "
        "should look like this county, not like a generic solar-and-chargers pack.\n"
        "Look for three kinds:\n"
        "- A need answered by a local strength or standout, such as a trade the county "
        "is strong in, spare land, a dam, a landfill, farms or a public building.\n"
        "- Something the county already has, made better, such as a plan to repair and "
        "upgrade an old building stock.\n"
        "- Two problems that solve each other, such as residents out of work and a local "
        "construction trade put to work on homes or public works that need it.\n"
        "Community solar, wind on reclaimed land and public EV charging may appear at "
        "most once each. Fill the rest with other project_types, or with "
        f'"{OTHER_PROJECT_TYPE}" ideas that only fit this county\'s standouts and sites.\n'
        "The factors also say what a project here has to look like. Heating and cooling "
        "degree days, hot days, rain and snow say which building upgrades matter most. "
        "Wind speed, terrain and land cover say what reclaimed or open land suits. The "
        "trades and industries residents work in say who could build and run a project. "
        "Social vulnerability, age, disability and broadband figures say who a program "
        "must reach first and how to reach them.\n"
        "Prefer opportunities the county government itself could start.\n\n"
        f"Rules:\n{rules}\n\n"
        "Format of each opportunity:\n"
        f"- title: at most {TITLE_MAX_WORDS} words.\n"
        f"- what: at most {TEXT_MAX_WORDS} words on what would be done.\n"
        f"- why_here: at most {TEXT_MAX_WORDS} words on which factors and sites make it fit "
        "this county, pointing to its data.\n"
        f"- plan: {MAX_STEPS - 2} to {MAX_STEPS} steps from start to finish for a county "
        f"official, at most {STEP_MAX_WORDS} words each.\n"
        f"- compliance: up to {MAX_COMPLIANCE} checks with owners or agencies, grounded in "
        f"input facts, at most {STEP_MAX_WORDS} words each.\n"
        f"- documents_needed: up to {MAX_DOCUMENTS} form or packet names to gather "
        f"(grant applications, leases, interconnection, environmental review), at most "
        f"{STEP_MAX_WORDS} words each. Names only; no URLs.\n"
        "- site_plans: one per site_id, each with course_of_action, compliance and "
        "documents_needed for that site. Empty when the opportunity is county-wide.\n\n"
        f"Also write summary: at most {SUMMARY_MAX_WORDS} words on the county's main needs "
        "and how your opportunities use what it has to meet them. The same rules apply.\n\n"
        "Plain sentences only: no headings, bullet characters or markdown.\n\n"
        f"County data:\n{json.dumps(digest, ensure_ascii=False, indent=1)}"
    )


async def _call_gemini(prompt: str, settings: Settings) -> ModelPlan:
    return await gemini.generate(prompt, ModelPlan, settings)


async def write_opportunities(
    digest: dict[str, Any], settings: Settings
) -> tuple[Plan | None, SourceStatus]:
    """Gemini's usable opportunities and summary, with a status entry. The plan
    is None when there is nothing usable, and the status says why. Never raises."""
    limit = settings.max_opportunities

    def none(reason: str) -> tuple[None, SourceStatus]:
        return None, SourceStatus(source=SOURCE, ok=False, cached=False, message=reason)

    if not digest["needs"]:
        return none("No needs were flagged, so there was nothing to write opportunities for")
    if not settings.gemini_api_key:
        return none("GEMINI_API_KEY is not set")

    prompt = build_prompt(digest, limit)
    key = {
        "model": settings.gemini_model,
        "prompt": hashlib.sha256(prompt.encode()).hexdigest(),
    }
    model: ModelPlan | None = None
    hit = cache_get(settings.cache_dir, "gemini_opportunities", key, 30 * DAY)
    if hit is not None:
        try:
            model = ModelPlan.model_validate(hit)
        except ValidationError:
            pass  # stale cache shape: call again
    cached = model is not None

    if model is None:
        try:
            model = await asyncio.wait_for(
                _call_gemini(prompt, settings), settings.gemini_timeout_s
            )
        except TimeoutError:
            return none(f"Gemini took longer than {settings.gemini_timeout_s:.0f} s")
        except Exception as exc:  # any SDK, network or parsing failure
            log.warning("Gemini opportunities call failed: %r", exc)
            return none(f"Gemini call failed ({gemini.describe_error(exc)})")

    kept, dropped = keep_valid(model, digest, limit)
    if dropped:
        log.warning("Gemini opportunities dropped: %s", "; ".join(dropped))
    if not kept:
        reason = dropped[0] if dropped else "it returned none"
        return none(f"Gemini's opportunities were rejected ({reason})")
    if not cached:
        cache_set(settings.cache_dir, "gemini_opportunities", key, model.model_dump())

    notes = []
    if dropped:
        count = len(model.opportunities)
        notes.append(f"{len(dropped)} of {count} opportunities dropped: {dropped[0]}")
    summary: str | None = model.summary.strip()
    problem = summary_problem(summary, digest)
    if problem:
        log.warning("Gemini summary rejected: %s", problem)
        notes.append(f"summary rejected ({problem}); template summary used")
        summary = None
    status = SourceStatus(source=SOURCE, ok=True, cached=cached, message="; ".join(notes) or None)
    return Plan(summary=summary, drafts=kept), status
