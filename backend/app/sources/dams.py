"""Non-powered dams with an estimate of the hydropower they could support.
Prepared by scripts/prep_data.py from ORNL HydroSource, "Technical Potential
for Hydropower Capacity at Nonpowered Dams" (Hansen et al., 2025).

The dataset covers 2,616 dams in the lower 48 states that an earlier ORNL
assessment gave at least 100 kW of theoretical potential. It is not a list of
every dam. Capacity and generation are model results from 1980-2015
streamflow and, for most dams, an estimated head; ORNL says they "do not
represent the actual numbers for engineering design".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "ORNL HydroSource non-powered dam assessment (2025)"


def dams_for_county(data_dir: Path, fips: str) -> list[dict[str, Any]]:
    return county_records(data_dir / "dams.parquet", fips)
