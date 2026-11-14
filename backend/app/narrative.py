"""County brief (CLAUDE.md section 12).

Gemini writes words; code supplies every number. The model sees a compact
payload of display-ready strings, and its output is rejected if it contains
any number that is not in that payload. On rejection, error, timeout or a
missing key, a deterministic template is used instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from app.cache import DAY, cache_get, cache_set
from app.config import Settings
from app.estimates import format_number
from app.models import Narrative, Need, Opportunity, OpportunityNote, SourceStatus

log = logging.getLogger(__name__)

SUMMARY_MAX_WORDS = 90
NOTE_MAX_WORDS = 60
MAX_GEMINI_CAVEATS = 4

# Included verbatim in the prompt (CLAUDE.md section 12).
PROMPT_RULES = (
    "Write for a county official with no technical background.",
    "Use only numbers that appear in the input. Do not calculate new ones.",
    "Mention only the funding programs in the input, with their status.",
    "Call every capacity, energy or household figure an estimate.",
    "Do not promise eligibility or award.",
)

STATUS_WORDS = {
    "open": "open",
    "open_with_deadlines": "open, with deadlines",
    "paused": "paused",
    "uncertain": "uncertain",
    "unverified": "not verified",
}


class _ModelNote(BaseModel):
    opportunity_id: str
    text: str


class ModelNarrative(BaseModel):
    """The response schema given to Gemini."""

    summary: str
    opportunity_notes: list[_ModelNote]
    caveats: list[str]


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
        "needs": [
            {
                "label": n.label,
                "level": n.level,
                "county_value": format_need_value(n.value, n.unit),
                "national_value": format_need_value(n.national_value, n.unit),
                "detail": n.detail,
            }
            for n in sorted(needs, key=lambda n: -n.severity)
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


def _words(text: str) -> int:
    return len(text.split())


def validation_error(narrative: ModelNarrative, payload: dict[str, Any]) -> str | None:
    """Why the model's narrative must be rejected, or None if it is usable."""
    if not narrative.summary.strip():
        return "empty summary"
    if _words(narrative.summary) > SUMMARY_MAX_WORDS:
        return f"summary is over {SUMMARY_MAX_WORDS} words"
    known_ids = {o["opportunity_id"] for o in payload["opportunities"]}
    for note in narrative.opportunity_notes:
        if note.opportunity_id not in known_ids:
            return "note refers to an opportunity that is not in the input"
        if _words(note.text) > NOTE_MAX_WORDS:
            return f"an opportunity note is over {NOTE_MAX_WORDS} words"

    allowed = extract_numbers(json.dumps(payload, ensure_ascii=False))
    written = " ".join(
        [narrative.summary, *(n.text for n in narrative.opportunity_notes), *narrative.caveats]
    )
    invented = sorted(extract_numbers(written) - allowed)
    if invented:
        shown = ", ".join(format_number(n) for n in invented[:5])
        return f"contains numbers that are not in the input: {shown}"
    return None


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
        lead = "The most severe is" if len(top) == 1 else "The most severe are"
        summary = f"{county} has {len(needs)} flagged {noun}. {lead} {_join(top)}."
        if opportunities:
            if len(opportunities) == 1:
                summary += (
                    f' 1 opportunity was matched to a local site: "{opportunities[0]["title"]}".'
                )
            else:
                summary += (
                    f" {len(opportunities)} opportunities were matched to specific local sites. "
                    f'The highest-ranked is "{opportunities[0]["title"]}".'
                )
        else:
            summary += " No local site could be matched to these needs from the data that loaded."

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


# --- Gemini --------------------------------------------------------------------


def build_prompt(payload: dict[str, Any]) -> str:
    rules = "\n".join(f"{i}. {rule}" for i, rule in enumerate(PROMPT_RULES, start=1))
    return (
        "You are writing a short brief about sustainability opportunities in one US county.\n\n"
        f"Rules:\n{rules}\n\n"
        "Format:\n"
        f"- summary: at most {SUMMARY_MAX_WORDS} words on the county's main needs and the "
        "best opportunities.\n"
        f"- opportunity_notes: one note per opportunity, at most {NOTE_MAX_WORDS} words, using "
        "the opportunity_id exactly as given.\n"
        f"- caveats: up to {MAX_GEMINI_CAVEATS} short sentences on what the reader should "
        "confirm before acting.\n"
        "- Copy numbers exactly as written in the input. Do not round, convert or combine them.\n"
        "- Plain sentences only: no headings, bullet points or markdown.\n\n"
        f"Input:\n{json.dumps(payload, ensure_ascii=False, indent=1)}"
    )


async def _call_gemini(prompt: str, settings: Settings) -> ModelNarrative:
    # Imported here so the app starts, and tests run, without the SDK configured.
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    # Config keys confirmed against GenerateContentConfig in google-genai 3.3.0.
    resp = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config={"response_mime_type": "application/json", "response_schema": ModelNarrative},
    )
    if isinstance(resp.parsed, ModelNarrative):
        return resp.parsed
    return ModelNarrative.model_validate_json(resp.text or "")


def _from_model(
    model: ModelNarrative, payload: dict[str, Any], failed_sources: list[str]
) -> Narrative:
    caveats = [c.strip() for c in model.caveats if c.strip()][:MAX_GEMINI_CAVEATS]
    if failed_sources:  # always stated by code, whatever the model wrote
        caveats.append(failed_sources_caveat(failed_sources))
    return Narrative(
        summary=model.summary.strip(),
        opportunity_notes=[
            OpportunityNote(opportunity_id=n.opportunity_id, text=n.text.strip())
            for n in model.opportunity_notes
        ],
        caveats=caveats or standard_caveats(payload, failed_sources),
        generated_by="gemini",
    )


async def write_narrative(
    payload: dict[str, Any], failed_sources: list[str], settings: Settings
) -> tuple[Narrative, SourceStatus]:
    """The narrative and a status entry saying how it was produced."""

    def fallback(reason: str) -> tuple[Narrative, SourceStatus]:
        return (
            template_narrative(payload, failed_sources),
            SourceStatus(source="gemini", ok=False, cached=False, message=reason),
        )

    if not payload["needs"] and not payload["opportunities"]:
        return fallback("Nothing to summarize; template narrative used")
    if not settings.gemini_api_key:
        return fallback("GEMINI_API_KEY is not set; template narrative used")

    key = {
        "model": settings.gemini_model,
        "payload": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    }
    hit = cache_get(settings.cache_dir, "gemini", key, 30 * DAY)
    if hit is not None:
        try:
            model = ModelNarrative.model_validate(hit)
            return (
                _from_model(model, payload, failed_sources),
                SourceStatus(source="gemini", ok=True, cached=True),
            )
        except ValidationError:
            pass  # stale cache shape: fall through and call again

    try:
        model = await asyncio.wait_for(
            _call_gemini(build_prompt(payload), settings), settings.gemini_timeout_s
        )
    except TimeoutError:
        return fallback(
            f"Gemini took longer than {settings.gemini_timeout_s:.0f} s; template narrative used"
        )
    except Exception as exc:  # any SDK, network or parsing failure
        log.warning("Gemini call failed: %r", exc)
        return fallback(f"Gemini call failed ({type(exc).__name__}); template narrative used")

    problem = validation_error(model, payload)
    if problem:
        log.warning("Gemini narrative rejected: %s", problem)
        return fallback(f"Gemini output rejected ({problem}); template narrative used")

    cache_set(settings.cache_dir, "gemini", key, model.model_dump())
    return (
        _from_model(model, payload, failed_sources),
        SourceStatus(source="gemini", ok=True, cached=False),
    )
