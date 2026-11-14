"""FEMA National Risk Index, county ratings. Prepared by scripts/prep_data.py
from FEMA's ArcGIS feature service (version 1.20, "December 2025").

Rating values seen in the data: "Very High", "Relatively High",
"Relatively Moderate", "Relatively Low", "Very Low", "No Rating",
"Insufficient Data".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.sources._parquet import county_records

# Hazards we track: prepared column -> display name. Version 1.20 reports
# "inland flooding" where earlier versions had "riverine flooding".
HAZARDS = {
    "inland_flood_rating": "Inland flooding",
    "wildfire_rating": "Wildfire",
    "drought_rating": "Drought",
    "heat_wave_rating": "Heat wave",
}


def source_label(version: str | None) -> str:
    return f"FEMA National Risk Index ({version})" if version else "FEMA National Risk Index"


def ratings_for_county(data_dir: Path, fips: str) -> dict[str, Any] | None:
    rows = county_records(data_dir / "nri_counties.parquet", fips)
    return rows[0] if rows else None
