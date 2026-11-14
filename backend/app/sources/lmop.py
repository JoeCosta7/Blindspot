"""EPA Landfill Methane Outreach Program database, one row per landfill.
Prepared by scripts/prep_data.py.

The database is not a complete inventory of US landfills, and about 300
landfills in it have no coordinates (they are placed in a county by name).

`is_candidate` is true when EPA's "Current Project Status" is "Candidate":
accepting waste or closed five years or less, at least one million tons of
waste, and no operational, under-construction or planned project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "EPA LMOP (September 2024)"


def landfills_for_county(data_dir: Path, fips: str) -> list[dict[str, Any]]:
    return county_records(data_dir / "lmop.parquet", fips)
