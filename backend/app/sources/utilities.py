"""Electric utilities serving each county, from EIA-861 2025. Prepared by
scripts/prep_data.py (step `eia861`).

EIA publishes which counties a utility serves, not how many customers it has
in each one. `residential_customers` and `residential_price_cents_kwh` are the
utility's figures for the whole state; the price is residential revenue over
residential sales for bundled service only, so it is null for utilities that
only deliver power or only sell energy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

SOURCE_LABEL = "EIA-861 2025"
FILE = "utilities.parquet"


def utilities_for_county(data_dir: Path, fips: str) -> list[dict[str, Any]]:
    """Largest utility (by in-state residential customers) first."""
    rows = county_records(data_dir / FILE, fips)
    return sorted(rows, key=lambda r: -(r.get("residential_customers") or 0))
