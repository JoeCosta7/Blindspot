"""EPA RE-Powering screening dataset: contaminated lands, landfills and mine
sites pre-screened for renewable energy. Prepared by scripts/prep_data.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "EPA RE-Powering (2022 dataset)"
SITE_TYPES = ("brownfield", "contaminated_site", "landfill", "mine_land")


def sites_for_county(data_dir: Path, fips: str) -> list[dict[str, Any]]:
    return county_records(data_dir / "repowering.parquet", fips)
