"""Curated funding table -> matches (CLAUDE.md section 11).

data/funding_programs.json is written by hand. Nothing here, and nothing in
the narrative step, adds or edits a program.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.models import FundingMatch, FundingStatus

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


@lru_cache(maxsize=4)
def load_programs(path: Path) -> tuple[FundingProgram, ...]:
    return tuple(FundingProgram.model_validate(item) for item in json.loads(path.read_text()))


def match_funding(
    programs: tuple[FundingProgram, ...] | list[FundingProgram],
    solution_id: str,
    asset_type: str,
) -> list[FundingMatch]:
    """A program matches when the solution is eligible and, if the program
    restricts asset types, the asset's type is one of them."""
    matched = [
        p
        for p in programs
        if solution_id in p.eligible_solutions
        and (p.eligible_asset_types is None or asset_type in p.eligible_asset_types)
    ]
    matched.sort(key=lambda p: (STATUS_ORDER[p.status], p.deadline or "9999", p.name))
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
        )
        for p in matched
    ]
