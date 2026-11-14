"""CDC/ATSDR Social Vulnerability Index 2022, county ranks. Prepared by
scripts/prep_data.py (step `svi`), which holds the original column names.

Ranks are percentiles among all US counties, 0 to 1, where higher means more
vulnerable. They are relative: 0.72 means more vulnerable than 72% of
counties, not 72% of anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "CDC/ATSDR Social Vulnerability Index 2022"
FILE = "svi_counties.parquet"


def svi_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    rows = county_records(data_dir / FILE, fips)
    return rows[0] if rows else None
