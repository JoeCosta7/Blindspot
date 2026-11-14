"""Energy communities for the clean electricity tax credit bonus, by county.
Prepared by scripts/prep_data.py (step `energy_communities`) from the
Treasury Office of Tax Analysis data sets:

- statistical area: counties in an MSA or non-MSA on the IRS Notice 2026-39
  Appendix 1 list (fossil fuel employment and 2025 unemployment), under
  either vintage of MSA groupings. The whole county qualifies until the 2027
  annual update.
- coal closure: census tracts with a closed coal mine (since 1999) or a
  retired coal generating unit (since 2009), and the tracts next to them,
  through Notice 2026-39. Only projects inside those tracts qualify.

The third category, brownfield sites, is per site and is not in this file.
Connecticut's rows use the counties it had before its 2022 planning regions,
so they do not match the 2025 county codes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "Treasury energy community data (IRS Notice 2026-39)"
FILE = "energy_communities.parquet"


def status_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    rows = county_records(data_dir / FILE, fips)
    return rows[0] if rows else None


def is_statistical_area(row: dict[str, Any] | None) -> bool:
    return bool(row and (row.get("statistical_area_v1") or row.get("statistical_area_v2")))


def coal_closure_tracts(row: dict[str, Any] | None) -> int:
    return int((row or {}).get("coal_closure_tracts") or 0)
