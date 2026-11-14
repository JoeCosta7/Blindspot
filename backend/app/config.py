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

    # --- request shape ----------------------------------------------------
    acs_year: int = 2024
    max_counties: int = 4
    # Share of a county's area that must fall inside a selection box for the
    # county to count when its centroid is outside the box.
    bbox_min_area_share: float = 0.25

    # --- timeouts (seconds) -----------------------------------------------
    source_timeout_s: float = 15.0
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

    # --- output caps ------------------------------------------------------
    max_opportunities: int = 6
    assets_per_solution: int = 2
    max_pvwatts_sites: int = 5
    # Some counties have thousands of RE-Powering sites or chargers. The map
    # gets the largest sites first, up to these limits; counts are still exact.
    max_sites_per_county: int = 150
    max_ev_markers_per_county: int = 300
    max_buildings_per_county: int = 300

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
