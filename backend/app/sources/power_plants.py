"""Power plants of 1 MW or more from EIA-860 2025 (final release). Prepared by
scripts/prep_data.py (step `eia860`), one row per plant.

`reuse_candidate` marks a plant with fossil units that have a planned
retirement date or that retired in 2010 or later: its grid connection and
land could host clean power or storage. A planned retirement is the
operator's plan as reported to EIA and can change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "EIA-860 2025"
FILE = "power_plants.parquet"


def plants_for_county(data_dir: Path, fips: str) -> list[dict[str, Any]]:
    return county_records(data_dir / FILE, fips)
