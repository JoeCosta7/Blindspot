"""USDA ERS Rural-Urban Continuum Codes 2023. Prepared by scripts/prep_data.py
(step `rucc`). Codes run from 1 (metro, 1 million people or more) to 9
(nonmetro, under 5,000 urban residents and not next to a metro area); 1-3 are
metro and 4-9 nonmetro.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "USDA ERS Rural-Urban Continuum Codes 2023"
FILE = "rucc.parquet"


def rucc_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    rows = county_records(data_dir / FILE, fips)
    return rows[0] if rows else None
