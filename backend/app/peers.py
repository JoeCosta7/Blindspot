"""Peer counties: the US counties most like this one in population, household
income and population density.

Similarity is our own choice, not a published classification. Each of the
three figures is put on a log scale (all three are heavily skewed: a few
counties are hundreds of times larger than most), standardized across every
county, and the peers are the nearest counties by straight-line distance over
the three. Density stands in for how rural a county is.

Each peer's Census needs are scored exactly as the report's own are, so the
two lists can be read side by side. Needs from other sources (EV chargers,
landfills, hazards and so on) are not compared: they are not measured for
every county up front.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from app import needs
from app.config import Settings
from app.models import Need, PeerCounty, PeerNeed
from app.sources import SourceError
from app.sources.census import AcsMetrics, Values, metrics_from_values

SQ_KM_PER_SQ_MILE = 2.589988  # exact: 1.609344 km squared

POPULATION = "B01003_001E"
INCOME = "B19013_001E"


@dataclass(frozen=True)
class CountyInfo:
    name: str  # "Broome County"
    state: str  # "NY"
    land_sq_miles: float | None


@lru_cache(maxsize=2)
def load_directory(path: Path) -> dict[str, CountyInfo]:
    """Name, state and land area for every county, from counties.geojson."""
    if not path.exists():
        raise SourceError(f"{path.name} is missing; run scripts/prep_data.py")
    directory: dict[str, CountyInfo] = {}
    for feature in json.loads(path.read_text())["features"]:
        props = feature["properties"]
        land_sq_km = props.get("land_sq_km")
        directory[props["GEOID"]] = CountyInfo(
            name=props.get("NAMELSAD") or props["NAME"],
            state=props["STUSPS"],
            land_sq_miles=land_sq_km / SQ_KM_PER_SQ_MILE if land_sq_km else None,
        )
    return directory


def density(population: float | None, info: CountyInfo | None) -> float | None:
    """People per square mile of land."""
    if not population or info is None or not info.land_sq_miles:
        return None
    return population / info.land_sq_miles


def nearest(
    fips: str, all_values: dict[str, Values], directory: dict[str, CountyInfo], count: int
) -> list[str]:
    """FIPS codes of the `count` counties most like `fips`, closest first.
    Empty when this county lacks one of the three figures."""
    codes: list[str] = []
    rows: list[tuple[float, float, float]] = []
    for code, values in all_values.items():
        population, income = values.get(POPULATION), values.get(INCOME)
        per_sq_mile = density(population, directory.get(code))
        if not population or not income or not per_sq_mile:
            continue
        codes.append(code)
        rows.append((math.log10(population), math.log10(income), math.log10(per_sq_mile)))
    if fips not in codes or len(codes) < 2:
        return []
    table = np.array(rows)
    spread = table.std(axis=0)
    spread[spread == 0] = 1.0
    scaled = (table - table.mean(axis=0)) / spread
    distance = np.linalg.norm(scaled - scaled[codes.index(fips)], axis=1)
    # Ties broken by FIPS so the list is the same on every run.
    order = sorted(range(len(codes)), key=lambda i: (distance[i], codes[i]))
    return [codes[i] for i in order if codes[i] != fips][:count]


def find_peers(
    fips: str,
    all_values: dict[str, Values],
    directory: dict[str, CountyInfo],
    national: AcsMetrics | None,
    flagged: list[Need],
    settings: Settings,
) -> list[PeerCounty]:
    own = {need.id for need in flagged}
    peers: list[PeerCounty] = []
    for code in nearest(fips, all_values, directory, settings.peer_count):
        info = directory[code]
        metrics = metrics_from_values(all_values[code])
        per_sq_mile = density(metrics.population, info)
        assert metrics.population and metrics.median_household_income and per_sq_mile
        peer_needs = needs.acs_needs(metrics, national, settings) if national else []
        peer_needs.sort(key=lambda need: (need.id not in own, -need.severity))
        peers.append(
            PeerCounty(
                fips=code,
                name=info.name,
                state=info.state,
                population=metrics.population,
                median_household_income=metrics.median_household_income,
                people_per_sq_mile=round(per_sq_mile, 1),
                needs=[
                    PeerNeed(id=need.id, label=need.label, shared=need.id in own)
                    for need in peer_needs
                ],
            )
        )
    return peers
