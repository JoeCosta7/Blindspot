"""EPA air quality design values, 2023-2025 (final, June 2026). Prepared by
scripts/prep_data.py (step `air`).

A county's design value is the highest among its monitors with a valid
value, so it describes the worst monitored spot, not the county average.
About 800 counties have a monitor; for the rest there is no row and nothing
is known. The standards are in config.py, read from the tables' notes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "EPA air quality design values 2023-2025"
FILE = "air_quality.parquet"


def design_values_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    rows = county_records(data_dir / FILE, fips)
    return rows[0] if rows else None
