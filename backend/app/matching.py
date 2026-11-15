"""Need + asset -> opportunity, ranked (CLAUDE.md section 9). Pure functions.

Gemini now writes the opportunities (opportunities.py). This module is used
for three things: the catalogue of project types Gemini may label an
opportunity with, the score, and the rule matches served when Gemini is
unavailable or its answer is rejected.

The rules live in data/solutions.json. `hosts` is ordered: for buildings the
first listed type is preferred, which keeps three building solutions from all
landing on the same school.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.estimates import has_estimate_inputs
from app.models import Asset, Need

MISSING_DATA_FACTOR = 0.7

# EPA's solar screening class for a site, best first. Sites EPA could not size
# (no acreage) sort last.
_PV_SCALE_RANK = {"utility": 0, "distributed": 1, "off_grid": 2}


# Kinds ranked by one size attribute, largest first.
_SIZE_ATTRIBUTE = {
    "landfill": "waste_in_place_tons",
    "dam": "capacity_mw",
    "wind_land": "epa_wind_mw",
    "plant": "reuse_mw",
}


class Solution(BaseModel):
    id: str
    name: str
    title_template: str
    # One plain sentence on what the project is, for the opportunity card.
    description: str | None = None
    # "county" solutions have no host site: they produce one county-wide
    # opportunity whenever a trigger need is flagged.
    # "plant": a power plant with fossil units retiring or retired.
    asset_kind: Literal["land", "building", "landfill", "dam", "wind_land", "plant", "county"]
    hosts: list[str]
    host_requires: str | None = None
    triggers: list[str]
    # For hazard_risk: the NRI hazards (sources/nri.py HAZARDS names) the
    # project answers. Empty means any flagged hazard counts.
    hazards: list[str] = []

    def answers(self, need: Need) -> bool:
        if need.id not in self.triggers:
            return False
        if need.id == "hazard_risk" and self.hazards:
            return any(h in (need.detail or "") for h in self.hazards)
        return True


@dataclass(frozen=True)
class Match:
    solution: Solution
    asset: Asset | None  # None for a county-wide solution
    needs: list[Need]
    score: int

    @property
    def id(self) -> str:
        return f"{self.solution.id}:{self.asset.id if self.asset else 'county'}"

    @property
    def title(self) -> str:
        """The project, not a place: Site Scout finds the building later. The
        host asset still backs the estimates."""
        if self.asset is None:
            return self.solution.title_template
        return self.solution.name
        return self.solution.title_template.format(asset=self.asset.name)


@lru_cache(maxsize=4)
def load_solutions(path: Path) -> tuple[Solution, ...]:
    return tuple(
        Solution.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))
    )


def distance(asset: Asset, lon: float, lat: float) -> float:
    """Approximate distance in degrees, good enough to rank within a county."""
    return math.hypot((asset.lon - lon) * math.cos(math.radians(lat)), asset.lat - lat)


def pick_assets(
    solution: Solution,
    assets: list[Asset],
    centroid: tuple[float, float],
    limit: int,
) -> list[Asset]:
    """Up to `limit` best host assets for a solution.

    Land: sites EPA screened as suitable for solar first, then largest acreage.
    Landfills: largest waste in place.
    Dams: largest modelled capacity. Wind land: largest EPA wind estimate.
    Buildings: preferred host type first, then nearest the county's geographic
    center (a stand-in for a population center, which we do not have).
    """
    candidates = [
        a
        for a in assets
        if a.type in solution.hosts
        and (solution.host_requires is None or a.attributes.get(solution.host_requires))
    ]
    if solution.asset_kind == "land":

        def key(a: Asset) -> tuple:
            acres = a.attributes.get("acres")
            return (
                _PV_SCALE_RANK.get(str(a.attributes.get("pv_scale")), 3),
                -(float(acres) if isinstance(acres, (int, float)) else 0.0),
                a.name,
            )

    elif solution.asset_kind in _SIZE_ATTRIBUTE:
        attribute = _SIZE_ATTRIBUTE[solution.asset_kind]

        def key(a: Asset) -> tuple:
            size = a.attributes.get(attribute)
            return (-(float(size) if isinstance(size, (int, float)) else 0.0), a.name)

    else:
        lon, lat = centroid

        def key(a: Asset) -> tuple:
            return (solution.hosts.index(a.type), distance(a, lon, lat), a.name)

    return sorted(candidates, key=key)[:limit]


def need_score(needs: list[Need]) -> float:
    """How much the needs an opportunity addresses count, 0 to 100: halfway
    between their highest priority and their mean priority. The mean alone
    marks a solution down for also answering lesser needs, and the highest
    alone ignores them."""
    priorities = [n.priority for n in needs]
    return (max(priorities) + sum(priorities) / len(priorities)) / 2


def need_rank_key(need: Need) -> tuple[int, int, str]:
    """The order of the Needs tab: priority, then severity, then id
    (the same key as needs.prioritize)."""
    return (-need.priority, -need.severity, need.id)


def top_need_rank(addressed: list[Need], flagged: list[Need]) -> int:
    """Where the highest-ranked need an opportunity addresses sits in the
    county's needs, as the Needs tab orders them: 0 for the top need.
    Opportunities are ordered by this first, so the list of opportunities
    follows the list of needs. One that addresses no flagged need goes last."""
    ranked = sorted(flagged, key=need_rank_key)
    position = {need.id: i for i, need in enumerate(ranked)}
    return min((position[n.id] for n in addressed if n.id in position), default=len(ranked))


def nearest_solution(
    solutions: tuple[Solution, ...] | list[Solution], need_ids: list[str]
) -> Solution | None:
    """The catalogued project type sharing the most needs with an idea of
    Gemini's own, for its funding leads. None when no type shares any."""
    best: tuple[int, Solution] | None = None
    for solution in solutions:
        shared = len(set(need_ids) & set(solution.triggers))
        if shared and (best is None or shared > best[0]):
            best = (shared, solution)
    return best[1] if best else None


def match_opportunities(
    needs: list[Need],
    assets: list[Asset],
    solutions: tuple[Solution, ...] | list[Solution],
    centroid: tuple[float, float],
    assets_per_solution: int = 2,
    max_opportunities: int = 6,
) -> list[Match]:
    """Up to `max_opportunities`, in the order of the needs they address, then
    by score. These are the rule matches the report falls back to when Gemini
    does not write the opportunities. `needs` should have their priority set (needs.prioritize).

    score = need score * asset factor. The factor is 1.0 if the asset has the
    data its estimate needs and 0.7 otherwise.
    A need's priority is its severity weighted by theme (needs.prioritize), so
    a solution that only answers transportation needs ranks below one that
    answers energy, pollution or resilience needs of the same severity.

    Each solution's best site is selected before any solution's second site, so
    a second charger location cannot push a whole solution off the list; among
    those, the ones meeting higher-ranked needs are selected first. The selected
    opportunities are then ordered by the top need they address, then by score.
    """
    matches: list[tuple[tuple, Match]] = []
    for order, solution in enumerate(solutions):
        matched = [n for n in needs if solution.answers(n)]
        if not matched:
            continue
        base = need_score(matched)
        need_rank = top_need_rank(matched, needs)
        picked: list[Asset | None]
        if solution.asset_kind == "county":
            picked = [None]
        else:
            picked = list(pick_assets(solution, assets, centroid, assets_per_solution))
        for rank, asset in enumerate(picked):
            has_data = has_estimate_inputs(solution.asset_kind, asset.attributes if asset else {})
            factor = 1.0 if has_data else MISSING_DATA_FACTOR
            score = round(base * factor)
            match = Match(solution=solution, asset=asset, needs=matched, score=score)
            matches.append(((rank, need_rank, -score, order), match))
    matches.sort(key=lambda m: m[0])
    selected = matches[:max_opportunities]
    # Top need addressed, then score, then first choices.
    selected.sort(key=lambda m: (m[0][1], m[0][2], m[0][0], m[0][3]))
    return [match for _, match in selected]
