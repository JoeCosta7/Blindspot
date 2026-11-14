"""USDA Forest Service Wildfire Risk to Communities v2, 2026 county summary
(RDS-2024-0030). Prepared by scripts/prep_data.py (step `wildfire`).

`risk_to_homes_national_rank` is the county's percentile among US counties
for mean Risk to Potential Structures (0-1, higher is riskier).
`housing_directly_exposed` is the share of housing units in the Direct
Exposure Zone, where homes sit in or next to burnable vegetation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "USFS Wildfire Risk to Communities (2026 county summary)"
FILE = "wildfire_counties.parquet"


def wildfire_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    rows = county_records(data_dir / FILE, fips)
    return rows[0] if rows else None
