"""Shared loader for the prepared national files. Each is read once and looked
up by county FIPS. Column names are the ones written by scripts/prep_data.py,
which is the only place that knows the original EPA and FEMA column names."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.sources import SourceError


@lru_cache(maxsize=8)
def _load(path: Path) -> dict[str, pd.DataFrame]:
    if not path.exists():
        raise SourceError(f"{path.name} is missing; run scripts/prep_data.py")
    frame = pd.read_parquet(path)
    return {str(fips): group for fips, group in frame.groupby("county_fips", sort=False)}


def county_records(path: Path, fips: str) -> list[dict[str, Any]]:
    group = _load(path).get(fips)
    if group is None:
        return []
    # Parquet nulls arrive as NaN or pd.NA; the API wants None.
    return [{k: _native(v) for k, v in row.items()} for row in group.to_dict(orient="records")]


def _native(value: Any) -> Any:
    if pd.isna(value):
        return None
    return value.item() if isinstance(value, np.generic) else value
