"""Curated funding table -> matches (CLAUDE.md section 11).

data/funding_programs.json is written by hand. Nothing here, and nothing in
the narrative step, adds or edits a program.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.models import FundingMatch, FundingStatus
from app.sources import energy_communities

# Display order: programs someone can act on come first.
STATUS_ORDER: dict[str, int] = {
    "open": 0,
    "open_with_deadlines": 1,
    "paused": 2,
    "uncertain": 3,
    "unverified": 4,
}


class FundingProgram(BaseModel):
    id: str
    name: str
    agency: str
    type: str
    eligible_applicants: list[str]
    eligible_solutions: list[str]
    eligible_asset_types: list[str] | None
    status: FundingStatus
    deadline: str | None
    key_terms: str
    source_url: str | None
    verified_on: str | None
    # The energy community bonus: matched only where the county, or the site,
    # falls in an energy community category (see `energy_community_note`).
    requires_energy_community: bool = False


@lru_cache(maxsize=4)
def load_programs(path: Path) -> tuple[FundingProgram, ...]:
    return tuple(FundingProgram.model_validate(item) for item in json.loads(path.read_text()))


def energy_community_note(
    status: dict[str, Any] | None, asset_type: str | None, checked: bool
) -> tuple[bool, str | None]:
    """Whether an energy community program applies, and why, from the Treasury
    data for the county (energy_communities.py) and the host site's type.

    `checked` is False when the energy community data did not load: the
    program is then listed as before, with a note saying it was not checked.
    """
    if not checked:
        return True, "Energy community status could not be checked for this county."
    if energy_communities.is_statistical_area(status):
        return True, (
            "The whole county is on the IRS statistical-area list (Notice 2026-39, "
            "fossil fuel employment and 2025 unemployment)."
        )
    tracts = energy_communities.coal_closure_tracts(status)
    if tracts:
        total = int((status or {}).get("total_tracts") or 0)
        share = f"{tracts} of {total}" if total else str(tracts)
        return True, (
            f"{share} census tracts in the county qualify through a coal mine closure or "
            "coal plant retirement; the project must be in one of them."
        )
    if asset_type == "brownfield":
        return True, (
            "The county is on neither IRS list, but brownfield sites are their own energy "
            "community category; confirm the site meets the federal brownfield definition."
        )
    return False, None


def match_funding(
    programs: tuple[FundingProgram, ...] | list[FundingProgram],
    solution_id: str,
    asset_type: str | None,
    energy_community: dict[str, Any] | None = None,
    energy_community_checked: bool = False,
) -> list[FundingMatch]:
    """A program matches when the solution is eligible and, if the program
    restricts asset types, the asset's type is one of them. A county-wide
    opportunity has no asset type and so never matches a restricted program.
    A program that requires an energy community matches only where the
    Treasury data say one applies."""
    matched: list[tuple[FundingProgram, str | None]] = []
    for p in programs:
        if solution_id not in p.eligible_solutions:
            continue
        if p.eligible_asset_types is not None and asset_type not in p.eligible_asset_types:
            continue
        note = None
        if p.requires_energy_community:
            applies, note = energy_community_note(
                energy_community, asset_type, energy_community_checked
            )
            if not applies:
                continue
        matched.append((p, note))
    matched.sort(key=lambda m: (STATUS_ORDER[m[0].status], m[0].deadline or "9999", m[0].name))
    return [
        FundingMatch(
            program_id=p.id,
            name=p.name,
            agency=p.agency,
            status=p.status,
            deadline=p.deadline,
            key_terms=p.key_terms,
            verified_on=p.verified_on,
            source_url=p.source_url,
            note=note,
        )
        for p, note in matched
    ]
