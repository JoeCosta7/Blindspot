"""DOE Low-Income Energy Affordability Data (LEAD) Tool, 2022 update: energy
burden by county. Prepared by scripts/prep_data.py (step `lead`).

Energy burden = household spending on electricity, gas and other fuels divided
by household income, each summed over the county's households as the LEAD
methodology does. "Low income" is below 200% of the federal poverty level.
The US row sums the 50 states and DC.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "DOE LEAD Tool (2022 update, ACS 2018-2022)"
FILE = "energy_burden.parquet"
NATIONAL_KEY = "US"


def burden_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    """{"county": row, "national": row}, or None when the county has no row."""
    county = county_records(data_dir / FILE, fips)
    national = county_records(data_dir / FILE, NATIONAL_KEY)
    if not county:
        return None
    return {"county": county[0], "national": national[0] if national else None}
