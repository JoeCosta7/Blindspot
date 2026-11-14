"""County brief: the summary and caveats shown above the opportunities.

Gemini writes the summary in the same call that writes the opportunities
(opportunities.py), and it is checked there. This module holds what code
writes: the payload the brief is built from, the caveats, and the
deterministic template used when Gemini's summary is missing or rejected.
"""

from __future__ import annotations

import re
from typing import Any

from app.estimates import format_number
from app.models import Narrative, Need, Opportunity, OpportunityNote

STATUS_WORDS = {
    "open": "open",
    "open_with_deadlines": "open, with deadlines",
    "paused": "paused",
    "uncertain": "uncertain",
    "unverified": "not verified",
}

AI_CAVEAT = (
    "The opportunities were written by Gemini from the needs, factors and sites in this "
    "report. Each was checked to use only this report's needs, sites and numbers. "
    "Feasibility and cost were not checked."
)


# --- payload -----------------------------------------------------------------


def format_need_value(value: float | None, unit: str) -> str | None:
    if value is None:
        return None
    if unit == "share":
        return f"{value * 100:.1f}%"
    if unit == "usd":
        return f"${value:,.0f}"
    if unit == "per_10k_residents":
        return f"{value:.1f} per 10,000 residents"
    if unit == "count":
        return f"{value:,.0f}"
    if unit == "usd_per_month":
        return f"${value:,.0f} a month"
    if unit == "lb_co2_per_mwh":
        return f"{value:,.0f} pounds of CO2 per MWh"
    return None


def build_payload(
    county_name: str, state: str, needs: list[Need], opportunities: list[Opportunity]
) -> dict[str, Any]:
    """Everything the narrative may refer to, and nothing else."""
    labels = {n.id: n.label for n in needs}
    return {
        "county": f"{county_name}, {state}",
        "need_count": len(needs),
        "opportunity_count": len(opportunities),
        "written_by_gemini": any(o.generated_by == "gemini" for o in opportunities),
        "needs": [
            {
                "label": n.label,
                "level": n.level,
                "county_value": format_need_value(n.value, n.unit),
                "national_value": format_need_value(n.national_value, n.unit),
                "detail": n.detail,
            }
            for n in sorted(needs, key=lambda n: (-n.priority, -n.severity))
        ],
        "opportunities": [
            {
                "opportunity_id": o.id,
                "title": o.title,
                "addresses": [labels[i] for i in o.need_ids if i in labels],
                "estimates": [
                    {
                        "label": e.label,
                        "value": f"{format_number(e.value)} {e.unit}",
                        "assumptions": e.assumptions,
                    }
                    for e in o.estimates
                ],
                "funding": [
                    {
                        "name": f.name,
                        "status": STATUS_WORDS[f.status],
                        "deadline": f.deadline,
                    }
                    for f in o.funding
                ],
            }
            for o in opportunities
        ],
    }


# --- validation ----------------------------------------------------------------

_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def extract_numbers(text: str) -> set[float]:
    """Every number in the text, ignoring thousands separators. Dates such as
    2027-01-21 yield 2027, 1 and 21."""
    return {float(token) for token in _NUMBER.findall(_THOUSANDS.sub("", text))}


# --- template ------------------------------------------------------------------


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def standard_caveats(payload: dict[str, Any], failed_sources: list[str]) -> list[str]:
    caveats = []
    opportunities = payload["opportunities"]
    if payload.get("written_by_gemini"):
        caveats.append(AI_CAVEAT)
    if any(o["estimates"] for o in opportunities):
        caveats.append(
            "Figures marked as estimates are rough. "
            "The assumptions behind each one are listed with it."
        )
    if any(o["funding"] for o in opportunities):
        caveats.append(
            "A listed funding program is a lead to check, not a finding that the "
            "county or project is eligible. Confirm status and deadlines with the agency."
        )
    if any("landfill" in o["title"].lower() for o in opportunities):
        caveats.append("EPA's landfill database does not include every landfill.")
    if any("state average" in (n.get("detail") or "").lower() for n in payload["needs"]):
        caveats.append(
            "Electricity bill and grid carbon figures are state averages, not county figures."
        )
    if failed_sources:
        caveats.append(failed_sources_caveat(failed_sources))
    return caveats


def failed_sources_caveat(failed_sources: list[str]) -> str:
    return (
        f"Some data could not be loaded ({_join(failed_sources)}), "
        "so needs or sites may be missing from this report."
    )


def template_narrative(payload: dict[str, Any], failed_sources: list[str]) -> Narrative:
    county = payload["county"]
    needs = payload["needs"]
    opportunities = payload["opportunities"]

    if not needs:
        summary = f"No needs were flagged for {county} from the data that loaded."
    else:
        top = [_lower_first(n["label"]) for n in needs[:3]]
        noun = "need" if len(needs) == 1 else "needs"
        lead = "The top priority is" if len(top) == 1 else "The top priorities are"
        summary = f"{county} has {len(needs)} flagged {noun}. {lead} {_join(top)}."
        if opportunities:
            verb = "written for" if payload.get("written_by_gemini") else "matched to"
            if len(opportunities) == 1:
                summary += f' 1 opportunity was {verb} these needs: "{opportunities[0]["title"]}".'
            else:
                summary += (
                    f" {len(opportunities)} opportunities were {verb} these needs. "
                    f'The highest-ranked is "{opportunities[0]["title"]}".'
                )
        else:
            summary += " No opportunity could be matched to these needs from the data that loaded."

    notes = []
    for o in opportunities:
        parts = []
        if o["addresses"]:
            parts.append(f"Addresses {_join([_lower_first(a) for a in o['addresses']])}.")
        if o["estimates"]:
            figures = "; ".join(f"{_lower_first(e['label'])} {e['value']}" for e in o["estimates"])
            parts.append(f"Rough estimates: {figures}.")
        if o["funding"]:
            first = o["funding"][0]
            deadline = f", deadline {first['deadline']}" if first["deadline"] else ""
            parts.append(f"Funding lead to check: {first['name']} ({first['status']}{deadline}).")
        else:
            parts.append("No program in the funding table matched.")
        notes.append(OpportunityNote(opportunity_id=o["opportunity_id"], text=" ".join(parts)))

    return Narrative(
        summary=summary,
        opportunity_notes=notes,
        caveats=standard_caveats(payload, failed_sources),
        generated_by="template",
    )


def compose(
    payload: dict[str, Any], failed_sources: list[str], gemini_summary: str | None
) -> Narrative:
    """The brief. `gemini_summary` is the checked summary from the opportunities
    call, or None to use the template's. Caveats are always written by code."""
    brief = template_narrative(payload, failed_sources)
    if gemini_summary is None:
        return brief
    return Narrative(
        summary=gemini_summary,
        opportunity_notes=[],
        caveats=brief.caveats,
        generated_by="gemini",
    )
