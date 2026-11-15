"""What kind of place fits an opportunity, decided per opportunity.

Gemini reads the opportunity (title, description, needs) and writes a host
spec: which sources to search and in what order, which kinds of public
building, which CoStar property types, a plain search phrase for Vexcel's
semantic image search, whether the site is judged as land or a building, and
which checks from data/site_checks.json matter. Code keeps only values from
the fixed lists below, so nothing invented reaches the scout. When Gemini is
unavailable or its answer is unusable, the spec comes from the catalogue
(profiles.profile_for), so every opportunity still gets one.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
from typing import Literal

from pydantic import BaseModel

from app import gemini
from app.cache import DAY, cache_get, cache_set
from app.config import Settings
from app.scout import checks, profiles

log = logging.getLogger(__name__)

SOURCES = {
    "search_report_sites": "sites the county report already found (types listed below)",
    "search_public_buildings": "the county's schools, libraries and colleges",
    "search_properties": "commercial property records (CoStar) by property type",
    "search_imagery": "Vexcel aerial imagery, searched by a plain description",
    "search_scans": "3D interior scans (Matterport) by building name",
}
REPORT_SITE_TYPES = {
    "brownfield": "brownfields",
    "contaminated_site": "Superfund and other cleanup sites",
    "landfill": "landfills (EPA RE-Powering)",
    "mine_land": "abandoned mine land",
    "lmop_landfill": "landfills in EPA's landfill gas database",
    "non_powered_dam": "dams without hydropower",
    "power_plant": "power plants",
}
PUBLIC_KINDS = ("library", "college", "school")
PROPERTY_TYPES = (
    "Industrial",
    "Retail",
    "Office",
    "Flex",
    "Hospitality",
    "Multifamily",
    "Specialty",
    "Healthcare",
    "SportsAndEntertainment",
    "Land",
)


class _AskedSpec(BaseModel):
    """The response schema given to Gemini."""

    looking_for: str
    sources: list[str]
    report_site_types: list[str]
    public_kinds: list[str]
    property_types: list[str]
    imagery_text: str
    judge_as: Literal["building", "land"]
    check_ids: list[str]


def _prompt(title: str, detail: str | None, need_labels: list[str], settings: Settings) -> str:
    table = checks.load_checks(settings.data_dir / "site_checks.json")
    check_lines = [f"- {c['id']}: {c.get('label') or c.get('fn')}" for c in table]
    return (
        "A county is looking for a specific place to carry out this project:\n"
        f"Project: {title}\n"
        + (f"About it: {detail}\n" if detail else "")
        + f"Needs it answers: {', '.join(need_labels) or 'not stated'}\n\n"
        "Decide what kind of place fits it best, and how to find one.\n"
        "- looking_for: at most 15 words, e.g. 'large paved parking lots at shopping centres'.\n"
        "- sources: the searches to run, best first, from:\n"
        + "\n".join(f"  {k}: {v}" for k, v in SOURCES.items())
        + "\n- report_site_types: for search_report_sites, from: "
        + ", ".join(f"{k} ({v})" for k, v in REPORT_SITE_TYPES.items())
        + f"\n- public_kinds: for search_public_buildings, from: {', '.join(PUBLIC_KINDS)}. "
        "Include a kind only if that kind of building truly suits the project.\n"
        f"- property_types: for search_properties, from: {', '.join(PROPERTY_TYPES)}.\n"
        "- imagery_text: at most 15 words describing what the place looks like from the air.\n"
        "- judge_as: 'building' if the project uses a building's roof or interior, 'land' if it "
        "uses open ground, pavement or a site.\n"
        "- check_ids: the checks that matter for this project, from:\n"
        + "\n".join(check_lines)
        + "\nUse only the values listed. Leave a list empty when it does not apply."
    )


def _clean(asked: _AskedSpec, settings: Settings) -> profiles.Profile | None:
    known_checks = {c["id"] for c in checks.load_checks(settings.data_dir / "site_checks.json")}
    sources = tuple(dict.fromkeys(s for s in asked.sources if s in SOURCES))
    site_types = tuple(dict.fromkeys(t for t in asked.report_site_types if t in REPORT_SITE_TYPES))
    kinds = tuple(dict.fromkeys(k for k in asked.public_kinds if k in PUBLIC_KINDS))
    types = tuple(dict.fromkeys(t for t in asked.property_types if t in PROPERTY_TYPES))
    check_ids = tuple(dict.fromkeys(c for c in asked.check_ids if c in known_checks))
    # A source needs what it searches for.
    sources = tuple(
        s
        for s in sources
        if not (s == "search_report_sites" and not site_types)
        and not (s == "search_public_buildings" and not kinds)
        and not (s == "search_properties" and not types)
        and not (s == "search_imagery" and not asked.imagery_text.strip())
    )
    if not sources:
        return None
    return profiles.Profile(
        source_order=sources,
        property_types=types,
        prefer_public=sources[0] == "search_public_buildings",
        hosts=" ".join(asked.looking_for.split()[:15]) or "suitable sites",
        land=asked.judge_as == "land",
        site_types=site_types,
        imagery_text=" ".join(asked.imagery_text.split()[:15]) or None,
        public_kinds=kinds,
        check_ids=check_ids,
        chosen_by="gemini",
    )


PUBLIC_WORDS = {
    "library": ("library", "libraries"),
    "college": ("college", "university", "campus"),
    "school": ("school",),
}
PROPERTY_WORDS = {
    "Industrial": ("industrial", "warehouse", "factory", "manufactur", "plant"),
    "Retail": ("retail", "store", "shop", "mall", "grocery", "storefront"),
    "Office": ("office",),
    "Flex": ("flex",),
    "Hospitality": ("hotel", "motel"),
    "Healthcare": ("clinic", "hospital", "health"),
    "Multifamily": ("apartment", "multifamily"),
}


def _mentioned(words: dict[str, tuple[str, ...]], text: str) -> list[str]:
    """Keys whose words appear in the text, in order of first appearance."""
    hits = []
    for key, forms in words.items():
        at = [text.find(f) for f in forms if f in text]
        if at:
            hits.append((min(at), key))
    return [key for _, key in sorted(hits)]


def follow_the_text(spec: profiles.Profile, text: str) -> profiles.Profile:
    """Put the building kinds and property types the opportunity itself names
    first, adding them if missing. A library project searches libraries first."""
    text = text.lower()
    named_kinds = _mentioned(PUBLIC_WORDS, text)
    named_types = _mentioned(PROPERTY_WORDS, text)
    kinds = tuple(dict.fromkeys([*named_kinds, *spec.public_kinds]))
    types = tuple(dict.fromkeys([*named_types, *spec.property_types]))
    sources = list(spec.source_order)
    if named_kinds:
        sources = [
            "search_public_buildings",
            *[s for s in sources if s != "search_public_buildings"],
        ]
    elif named_types and "search_properties" not in sources:
        sources.append("search_properties")
    return dataclasses.replace(
        spec,
        source_order=tuple(sources),
        public_kinds=kinds,
        property_types=types,
    )


def from_catalogue(solution_id: str | None, title: str, hosts: list[str]) -> profiles.Profile:
    """The catalogue's profile, with public building kinds from the project's hosts."""
    base = profiles.profile_for(solution_id, title)
    # Only the public building kinds the project is catalogued for; none for an
    # idea of Gemini's own, so it never defaults to schools.
    kinds = tuple(k for k in hosts if k in PUBLIC_KINDS) or base.public_kinds
    sources = tuple(s for s in base.source_order if s != "search_public_buildings" or kinds)
    return dataclasses.replace(
        base,
        source_order=sources,
        # Whether county-owned buildings rank higher is the catalogue's policy
        # for the project type, not a side effect of the search order.
        prefer_public=base.prefer_public,
        public_kinds=kinds,
        chosen_by="catalogue",
    )


async def resolve(
    title: str,
    detail: str | None,
    need_labels: list[str],
    solution_id: str | None,
    catalogue_hosts: list[str],
    settings: Settings,
) -> profiles.Profile:
    """The host spec for one opportunity. Never raises."""
    text = f"{title} {detail or ''}"
    fallback = follow_the_text(from_catalogue(solution_id, title, catalogue_hosts), text)
    if not settings.gemini_api_key:
        return fallback
    prompt = _prompt(title, detail, need_labels, settings)
    key = {"model": settings.gemini_model, "prompt": hashlib.sha256(prompt.encode()).hexdigest()}
    hit = cache_get(settings.cache_dir, "scout_host_spec", key, 30 * DAY)
    try:
        asked = (
            _AskedSpec.model_validate(hit)
            if hit
            else await asyncio.wait_for(
                gemini.generate(prompt, _AskedSpec, settings), settings.gemini_timeout_s
            )
        )
    except Exception as exc:  # SDK, network, timeout or parsing
        log.warning("host spec failed: %r", exc)
        return fallback
    spec = _clean(asked, settings)
    if spec is None:
        log.warning("host spec unusable: %s", json.dumps(asked.model_dump())[:300])
        return fallback
    if not hit:
        cache_set(settings.cache_dir, "scout_host_spec", key, asked.model_dump())
    # Gemini chooses where to look; the ranking policy stays the catalogue's.
    return follow_the_text(
        dataclasses.replace(
            spec, prefer_public=fallback.prefer_public, judge_interior=fallback.judge_interior
        ),
        text,
    )
