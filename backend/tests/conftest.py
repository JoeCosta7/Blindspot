from pathlib import Path

import pytest
from shapely.geometry import box

from app import cache
from app.config import BACKEND_DIR, Settings
from app.geo import County, CountyIndex
from app.models import Asset, Need
from app.sources.census import AcsMetrics


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """No keys, no .env, and a throwaway cache. Hand-written data files
    (solutions, funding) are read from the real data directory."""
    cache.clear_memory()
    return Settings(
        _env_file=None,
        census_api_key="",
        nlr_api_key="",
        gemini_api_key="",
        cache_dir=tmp_path / "cache",
        data_dir=BACKEND_DIR / "data",
    )


def make_county(fips: str, west: float, south: float, east: float, north: float) -> County:
    return County(
        fips=fips,
        name=f"County {fips}",
        state="ZZ",
        state_fips=fips[:2],
        geometry=box(west, south, east, north),
        centroid_lon=(west + east) / 2,
        centroid_lat=(south + north) / 2,
    )


@pytest.fixture
def grid_index() -> CountyIndex:
    """Six 1x1 degree counties in a 3x2 grid, FIPS 99001-99006, west to east
    then south to north."""
    counties = []
    for row in range(2):
        for col in range(3):
            fips = f"99{row * 3 + col + 1:03d}"
            counties.append(make_county(fips, col, row, col + 1, row + 1))
    return CountyIndex(counties)


def make_need(need_id: str, severity: int, level: str = "high") -> Need:
    return Need(
        id=need_id,
        label=need_id.replace("_", " ").capitalize(),
        theme="energy",
        value=0.5,
        national_value=0.25,
        unit="share",
        severity=severity,
        level=level,
        source="test",
    )


def make_asset(
    asset_id: str, asset_type: str, lon: float = 0.5, lat: float = 0.5, **attrs
) -> Asset:
    return Asset(
        id=asset_id,
        type=asset_type,
        name=f"Site {asset_id}",
        lat=lat,
        lon=lon,
        attributes=attrs,
        source="test",
    )


def make_metrics(**overrides) -> AcsMetrics:
    base = {
        "population": 100_000,
        "median_household_income": 80_000.0,
        "poverty_rate": 0.12,
        "unemployment_rate": 0.05,
        "pre1980_housing_share": 0.50,
        "delivered_fuel_heat_share": 0.10,
        "electric_heat_share": 0.40,
        "mobile_home_share": 0.06,
        "renter_share": 0.35,
        "no_vehicle_share": 0.08,
        "drive_alone_share": 0.70,
        "long_commute_share": 0.16,
    }
    return AcsMetrics(**{**base, **overrides})
