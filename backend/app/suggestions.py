"""Solutions suggested by Gemini.

The opportunities from matching.py only cover the solutions in solutions.json.
Here Gemini reads a digest of the county and proposes further solutions to its
flagged needs, in words.

Two steps:

1. `build_digest` cuts the report down to what a planner would read: the
   flagged needs, a few headline figures, and for each kind of site a count
   and the best few examples. The model never sees the full asset list.
2. `suggest_solutions` sends the digest, then checks every suggestion against
   it. A suggestion is dropped if it names a need or site that is not in the
   digest, or contains a number that is not in the digest. There is no
   template fallback: with no usable suggestion the list is empty.

This departs from CLAUDE.md rule 2 on purpose: Gemini chooses the solutions
here. It still supplies no numbers, and no funding programs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ValidationError

from app import gemini
from app.cache import DAY, cache_get, cache_set
from app.config import Settings
from app.estimates import format_number, round_sig
from app.models import (
    Asset,
    CountyContext,
    Need,
    Opportunity,
    Resources,
    SourceStatus,
    SuggestedSolution,
)
from app.narrative import extract_numbers, format_need_value

log = logging.getLogger(__name__)

SOURCE = "gemini_solutions"

# Lengths asked for in the prompt. Models overshoot word counts a little, so a
# suggestion is only dropped past the limit times LENGTH_SLACK.
TITLE_MAX_WORDS = 12
TEXT_MAX_WORDS = 45
STEP_MAX_WORDS = 20
MAX_STEPS = 3
LENGTH_SLACK = 1.3

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
}
# Counted but never listed: nobody builds a project at an existing charger.
COUNT_ONLY_TYPES = {"ev_station"}

PROMPT_RULES = (
    "Write for a county official with no technical background.",
    'Every solution must directly reduce at least one need listed under "needs". Put '
    "only the need_id values it directly reduces in need_ids, exactly as given.",
    "Base each solution on this county's own data, and use a place listed under "
    '"sites" where one fits. Put the site_id values of the places it would use in '
    "site_ids, exactly as given. Leave site_ids empty only for a county-wide program.",
    "State nothing about a site that the input does not say.",
    'Do not repeat a project listed under "already_in_report". Propose different '
    "solutions, or ones that build on those projects.",
    "Use only numbers that appear in the input, copied exactly. Do not calculate, "
    "round or estimate: no costs, savings, sizes, percentages, dates or time frames "
    "of your own.",
    "Do not name funding programs, grants or tax credits, and do not promise results.",
    'Do not rely on a kind of site listed under "not_found".',
)


class _ModelSolution(BaseModel):
    title: str
    need_ids: list[str]
    site_ids: list[str]
    what: str
    why_here: str
    first_steps: list[str]


class ModelSuggestions(BaseModel):
    """The response schema given to Gemini."""

    solutions: list[_ModelSolution]


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


def _figures(
    population: int | None, context: CountyContext, resources: Resources
) -> dict[str, str]:
    def share(value: float | None) -> str | None:
        return f"{value * 100:.1f}%" if value is not None else None

    income = context.median_household_income
    price = context.state_electricity_price_cents_kwh
    carbon = context.state_grid_co2_lb_per_mwh
    solar = resources.solar_kwh_per_kw_year
    figures = {
        "Population": f"{population:,}" if population is not None else None,
        "Median household income": f"${income:,.0f}" if income is not None else None,
        "Poverty rate": share(context.poverty_rate),
        "Unemployment rate": share(context.unemployment_rate),
        "Homes with electric heat": share(context.electric_heat_share),
        "Electricity price (state average)": (
            f"{price:.1f} cents per kWh" if price is not None else None
        ),
        "Grid carbon intensity (state average)": (
            f"{carbon:,.0f} pounds of CO2 per MWh" if carbon is not None else None
        ),
        "Solar yield": f"{solar:,.0f} kWh a year per kW of panels" if solar else None,
        "Land in farms": _amount(resources.land_in_farms_acres, "acres"),
        "Cropland": _amount(resources.cropland_acres, "acres"),
    }
    return {label: value for label, value in figures.items() if value}


def _compact(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if value not in (None, "", [])}


def build_digest(
    county_name: str,
    state: str,
    population: int | None,
    context: CountyContext,
    resources: Resources,
    needs: list[Need],
    assets: list[Asset],
    missing_types: list[str],
    opportunities: list[Opportunity],
    sites_per_type: int,
) -> dict[str, Any]:
    """What the model may reason from, and nothing else. `assets` is every
    asset found, best first within its type; only `sites_per_type` of each
    type are listed, with the exact count beside them. Sites an opportunity
    already uses are listed first, so the model can build on them."""
    by_type: dict[str, list[Asset]] = {}
    for asset in assets:
        by_type.setdefault(asset.type, []).append(asset)
    in_report = {o.asset_id for o in opportunities if o.asset_id}

    sites = []
    for asset_type, label in ASSET_LABELS.items():
        found = sorted(by_type.get(asset_type, []), key=lambda a: a.id not in in_report)
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
        "figures": _figures(population, context, resources),
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
            for n in sorted(needs, key=lambda n: -n.severity)
        ],
        "sites": sites,
        "not_found": [ASSET_LABELS.get(t, t) for t in missing_types],
        "already_in_report": [
            _compact(
                {
                    "project": o.title,
                    "estimates": [
                        f"{e.label}: {format_number(e.value)} {e.unit}" for e in o.estimates
                    ],
                }
            )
            for o in opportunities
        ],
    }


def _site_ids(digest: dict[str, Any]) -> set[str]:
    return {
        example["site_id"] for group in digest["sites"] for example in group.get("examples", [])
    }


def _prose(value: Any) -> Iterator[str]:
    """Every value in the digest except site ids, whose digits are not figures."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key != "site_id":
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


def solution_problem(solution: _ModelSolution, digest: dict[str, Any]) -> str | None:
    """Why one suggestion must be dropped, or None if it is usable."""
    if not solution.title.strip() or not solution.what.strip() or not solution.why_here.strip():
        return "a required field is empty"
    known_needs = {n["need_id"] for n in digest["needs"]}
    if not solution.need_ids:
        return "it addresses no need"
    if not set(solution.need_ids) <= known_needs:
        return "it names a need that is not in the input"
    if not set(solution.site_ids) <= _site_ids(digest):
        return "it names a site that is not in the input"
    steps = [step for step in solution.first_steps if step.strip()]
    if not steps:
        return "it has no first steps"
    if (
        _over(solution.title, TITLE_MAX_WORDS)
        or _over(solution.what, TEXT_MAX_WORDS)
        or _over(solution.why_here, TEXT_MAX_WORDS)
        or any(_over(step, STEP_MAX_WORDS) for step in steps)
    ):
        return "it is too long"
    written = " ".join([solution.title, solution.what, solution.why_here, *steps])
    invented = sorted(extract_numbers(written) - allowed_numbers(digest))
    if invented:
        shown = ", ".join(format_number(n) for n in invented[:5])
        return f"it contains numbers that are not in the input: {shown}"
    return None


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def keep_valid(
    model: ModelSuggestions, digest: dict[str, Any], limit: int
) -> tuple[list[SuggestedSolution], list[str]]:
    """The usable suggestions, at most `limit`, and why each other one was dropped."""
    kept: list[SuggestedSolution] = []
    dropped: list[str] = []
    titles: set[str] = set()
    for solution in model.solutions:
        title = solution.title.strip()
        problem = solution_problem(solution, digest)
        if problem is None and title.lower() in titles:
            problem = "it repeats another suggestion"
        if problem:
            dropped.append(problem)
            continue
        if len(kept) >= limit:
            continue  # more than asked for; not a fault
        titles.add(title.lower())
        kept.append(
            SuggestedSolution(
                id=f"ai:{len(kept) + 1}",
                title=title,
                summary=solution.what.strip(),
                rationale=solution.why_here.strip(),
                need_ids=_unique(solution.need_ids),  # type: ignore[arg-type]
                asset_ids=_unique(solution.site_ids),
                first_steps=[s.strip() for s in solution.first_steps if s.strip()][:MAX_STEPS],
            )
        )
    return kept, dropped


# --- Gemini ----------------------------------------------------------------------


def build_prompt(digest: dict[str, Any], limit: int) -> str:
    rules = "\n".join(f"{i}. {rule}" for i, rule in enumerate(PROMPT_RULES, start=1))
    return (
        "You are advising a US county government on sustainability projects. From the "
        f"county data below, propose up to {limit} practical solutions to the county's "
        "flagged needs. Prefer solutions the county government itself could start.\n\n"
        f"Rules:\n{rules}\n\n"
        "Format of each solution:\n"
        f"- title: at most {TITLE_MAX_WORDS} words.\n"
        f"- what: at most {TEXT_MAX_WORDS} words on what would be done.\n"
        f"- why_here: at most {TEXT_MAX_WORDS} words on why it fits this county, "
        "pointing to its data.\n"
        f"- first_steps: 2 or {MAX_STEPS} actions the county could take first, at most "
        f"{STEP_MAX_WORDS} words each.\n"
        "- Plain sentences only: no headings, bullet characters or markdown.\n\n"
        f"County data:\n{json.dumps(digest, ensure_ascii=False, indent=1)}"
    )


async def _call_gemini(prompt: str, settings: Settings) -> ModelSuggestions:
    return await gemini.generate(prompt, ModelSuggestions, settings)


async def suggest_solutions(
    digest: dict[str, Any], settings: Settings
) -> tuple[list[SuggestedSolution], SourceStatus]:
    """Gemini's usable suggestions and a status entry. Never raises."""
    limit = settings.max_ai_solutions

    def none(reason: str) -> tuple[list[SuggestedSolution], SourceStatus]:
        return [], SourceStatus(source=SOURCE, ok=False, cached=False, message=reason)

    if limit <= 0:
        return none("AI suggestions are turned off (MAX_AI_SOLUTIONS is 0)")
    if not digest["needs"]:
        return none("No needs were flagged, so there was nothing to suggest solutions for")
    if not settings.gemini_api_key:
        return none("GEMINI_API_KEY is not set; no AI suggestions were requested")

    prompt = build_prompt(digest, limit)
    key = {
        "model": settings.gemini_model,
        "prompt": hashlib.sha256(prompt.encode()).hexdigest(),
    }
    model: ModelSuggestions | None = None
    hit = cache_get(settings.cache_dir, SOURCE, key, 30 * DAY)
    if hit is not None:
        try:
            model = ModelSuggestions.model_validate(hit)
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
            log.warning("Gemini suggestions call failed: %r", exc)
            return none(f"Gemini call failed ({gemini.describe_error(exc)})")

    kept, dropped = keep_valid(model, digest, limit)
    if dropped:
        log.warning("Gemini suggestions dropped: %s", "; ".join(dropped))
    if not kept:
        reason = dropped[0] if dropped else "it returned none"
        return none(f"Gemini's suggestions were rejected ({reason})")
    if not cached:
        cache_set(settings.cache_dir, SOURCE, key, model.model_dump())

    count = len(model.solutions)
    message = f"{len(dropped)} of {count} suggestions dropped: {dropped[0]}" if dropped else None
    return kept, SourceStatus(source=SOURCE, ok=True, cached=cached, message=message)
