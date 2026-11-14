"""CDC PLACES 2025 release, county estimates (2023 BRFSS). Prepared by
scripts/prep_data.py (step `places`).

Values are crude prevalence among adults, as shares. They are model-based
estimates for small areas, not counts. The social-needs measures (utility
shut-off threat, food and housing insecurity, lack of transportation) come
from an optional survey module and are missing in states that skipped it.
The US row is our own: the county average weighted by adult population.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "CDC PLACES 2025 (BRFSS 2023)"
FILE = "places_counties.parquet"
NATIONAL_KEY = "US"
NATIONAL_NOTE = "US figure: average of all counties, weighted by adult population."


def places_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    """{"county": row, "national": row}, or None when the county has no row."""
    county = county_records(data_dir / FILE, fips)
    national = county_records(data_dir / FILE, NATIONAL_KEY)
    if not county:
        return None
    return {"county": county[0], "national": national[0] if national else None}
