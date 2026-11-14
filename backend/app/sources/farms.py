"""Farmland and large livestock farms per county, with the number of manure
digesters EPA knows of. Prepared by scripts/prep_data.py from the USDA 2022
Census of Agriculture (NASS bulk file) and the EPA AgSTAR digester database.

The census publishes counts of farms by herd size, not farm locations, so
there are no map points. At county level its largest herd classes are 500 or
more milk cows and 1,000 or more hogs. Connecticut is missing: the 2022 census
used counties the state has since replaced with planning regions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

CENSUS_LABEL = "USDA Census of Agriculture 2022"
SOURCE_LABEL = f"{CENSUS_LABEL}; EPA AgSTAR digester database"


def farms_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    rows = county_records(data_dir / "farms.parquet", fips)
    return rows[0] if rows else None
