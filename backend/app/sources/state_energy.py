"""State electricity bills (EIA) and grid carbon intensity (EPA eGRID).
Prepared by scripts/prep_data.py into data/state_energy.json.

Both figures are state averages: neither agency publishes them by county. The
eGRID rate is for electricity generated in the state, which is not exactly
what the state's homes use, because power crosses state lines.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.sources import SourceError

NATIONAL_KEY = "US"


@lru_cache(maxsize=4)
def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SourceError(f"{path.name} is missing; run scripts/prep_data.py energy")
    return json.loads(path.read_text(encoding="utf-8"))


def bill_source_label(year: int) -> str:
    return f"EIA residential sales data, {year} (state average)"


def grid_source_label(year: int) -> str:
    return f"EPA eGRID{year} (state average)"


def for_state(data_dir: Path, state: str) -> dict[str, Any] | None:
    """`{"state": {...}, "national": {...}, "electricity_year", "grid_year"}`,
    or None when the file has nothing for the state."""
    data = _load(data_dir / "state_energy.json")
    figures = data["states"].get(state)
    if not figures:
        return None
    return {
        "state_abbr": state,
        "state": figures,
        "national": data["states"].get(NATIONAL_KEY, {}),
        "electricity_year": data["electricity_year"],
        "grid_year": data["grid_year"],
    }
