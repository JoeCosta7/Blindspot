"""Settings, read from backend/.env. Scoring thresholds live here too."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- keys -------------------------------------------------------------
    census_api_key: str = ""
    nlr_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # Geoapify Places API: OpenStreetMap schools, libraries and colleges.
    geoapify_api_key: str = ""
    # Google Earth Engine (surface heat, tree canopy). Empty = source disabled.
    ee_project: str = ""
    # Earth Engine results change only when the imagery does, so cache them long.
    ee_cache_ttl_days: int = 30
    # Earth Engine can run past the shared 15 s timeout on very large counties.
    ee_timeout_s: float = 25.0

    # --- request shape ----------------------------------------------------
    acs_year: int = 2024
    max_counties: int = 4
    # Share of a county's area that must fall inside a selection box for the
    # county to count when its centroid is outside the box.
    bbox_min_area_share: float = 0.25

    # --- timeouts (seconds) -----------------------------------------------
    source_timeout_s: float = 15.0
    # Public buildings for the largest counties take several Geoapify
    # requests (Cook County, IL: 13 requests, 19 s on a cold cache). Most
    # counties need one request and finish in about a second.
    buildings_timeout_s: float = 45.0
    gemini_timeout_s: float = 20.0

    # --- need scoring (CLAUDE.md section 8) -------------------------------
    need_ratio_elevated: float = 1.10
    need_ratio_high: float = 1.25
    # severity = 100 * clamp((ratio - 1) / span, 0, 1)
    need_severity_span: float = 0.5
    # Severity for needs that come from a category, not a ratio. These are our
    # own scoring choices, not published figures.
    severity_very_high: int = 100
    severity_high: int = 75
    severity_elevated: int = 40
    # Public EV stations per 10,000 residents used as the national benchmark.
    # Left unset, the pipeline computes it per request from two cached calls:
    # the NLR national station count and the ACS national population.
    ev_benchmark_per_10k: float | None = None

    # --- Earth Engine needs (computed 2026-11-14) -----------------------------
    # Both figures use ESA WorldCover 2021 "built-up" as the developed land, so
    # they describe buildings and pavement, not lawns and parks.
    #
    # Urban heat = mean summer (Jun-Aug 2021-2025) Landsat 8/9 surface temperature
    # on built-up land minus the same on natural land (tree, shrub, grass, crop,
    # wetland), in the county. Reference and thresholds are our own choices read
    # off 120 random lower-48 counties measured the same way on 2026-11-14:
    # sample median 5.1 C, 75th percentile 6.6 C, 90th percentile 8.0 C. They are
    # not published standards.
    heat_island_reference_c: float = 5.1
    heat_island_elevated_c: float = 6.5
    heat_island_high_c: float = 8.0
    # Tree canopy on built-up land: USFS Tree Canopy Cover 2025 averaged over
    # built-up land in the lower 48 (computed 2026-11-14 at 250 m: 13.3%; the
    # same sample's county median was 13.1%). Scored like an ACS need, with
    # lower canopy as the worse side.
    tree_canopy_built_up_national: float = 0.133
    # Below this share of the county's area, built-up land is a few pixels at the
    # 240 m scale we reduce at, too few to describe a county. Our own cut-off.
    ee_min_built_up_share: float = 0.001

    # --- output caps ------------------------------------------------------
    max_opportunities: int = 6
    assets_per_solution: int = 2
    max_pvwatts_sites: int = 5
    # Some counties have thousands of RE-Powering sites or chargers. The map
    # gets the largest sites first, up to these limits; counts are still exact.
    max_sites_per_county: int = 150
    max_ev_markers_per_county: int = 300
    max_buildings_per_county: int = 300

    # --- Gemini-suggested solutions (suggestions.py) ----------------------
    # Most suggestions to ask for and show. 0 turns the feature off.
    max_ai_solutions: int = 4
    # Sites of each type listed in the digest sent to Gemini, best first. The
    # digest gives the exact count for the rest.
    digest_sites_per_type: int = 3

    # --- paths ------------------------------------------------------------
    data_dir: Path = BACKEND_DIR / "data"
    cache_dir: Path = BACKEND_DIR / ".cache"

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def acs_label(self) -> str:
        return f"ACS {self.acs_year - 4}-{self.acs_year}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
