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
    gemini_model: str = "gemini-3.5-flash"
    # Geoapify Places API: schools, libraries and colleges.
    geoapify_api_key: str = ""
    # Site Scout layers (app/scout/). Vexcel: long-life key in the X-API-KEY
    # header. Matterport: API token id and secret, HTTP Basic. The Matterport
    # SDK key is for browser embeds and is not used by the backend.
    vexcel_api_key: str = ""
    matterport_api_key: str = ""
    matterport_api_secret: str = ""
    # The CoStar hackathon data kit (static files, no key). Missing = no CoStar layer.
    costar_kit_dir: Path = BACKEND_DIR.parent / "costar-hackathon-kit"
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
    gemini_timeout_s: float = 60.0

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

    # --- sustainability priority (ranking) --------------------------------
    # priority = severity * a weight. Needs and opportunities are ranked by
    # priority, not severity. Severity says how far the county is from the US
    # figure; the weight says how much that gap counts for the sustainability
    # track. Without it the EV charging gap, which sits at severity 100 in most
    # counties, led almost every report.
    # These weights are our own choice, not published figures. Three tiers:
    #   1.0  pollution and what clean power displaces: the waste theme (landfill
    #        gas, farm waste) and the carbon-intensive grid; plus natural hazard
    #        risk, the only need that carries water (flood, drought).
    #   0.7  household energy, heat and tree cover, and equity.
    #   0.5  transportation.
    # 0.7 is below 0.75 on purpose: a need with no US figure to compare against
    # stops at severity_high (75), and this lets a top-tier one of those outrank
    # a middle-tier need that reached severity 100.
    # A theme left out counts in full.
    # Added 2026-11-14: "water" (unsafe drinking water, impaired streams) and
    # "health" (air pollution) are pollution needs and join the top tier.
    need_priority_weights: dict[str, float] = {
        "waste": 1.0,
        "water": 1.0,
        "health": 1.0,
        "energy": 0.7,
        "resilience": 0.7,
        "equity": 0.7,
        "transportation": 0.5,
    }
    # Weights by need id. One of these replaces the weight of the need's theme.
    need_priority_overrides: dict[str, float] = {
        "carbon_intensive_grid": 1.0,
        "hazard_risk": 1.0,
    }
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

    # --- air quality (EPA design values 2023-2025) -------------------------
    # Levels of the national standards, as stated in the notes of EPA's design
    # value tables (read 2026-11-14): 2024 annual PM2.5 9.0 ug/m3, 2006 24-hour
    # PM2.5 35 ug/m3, 2015 8-hour ozone 0.070 ppm.
    pm25_annual_standard_ugm3: float = 9.0
    pm25_24h_standard_ugm3: float = 35.0
    ozone_standard_ppm: float = 0.070
    # A design value at or above this share of its standard flags the need as
    # "elevated"; at or above the standard, "high". Our own cut-off.
    air_elevated_share_of_standard: float = 0.9

    # --- water ---------------------------------------------------------------
    # Fewer assessed water bodies than this in a county is too few for a
    # share; no impaired-waters need is flagged. Our own cut-off.
    attains_min_assessed: int = 5

    # --- history window --------------------------------------------------------
    # Years of FEMA major disaster declarations to count.
    disaster_history_years: int = 20

    # --- output caps ------------------------------------------------------
    # Most opportunities to ask Gemini for, and to take from the rule matches
    # when Gemini is unavailable.
    max_opportunities: int = 6
    # Rule matches only: sites offered per project type.
    assets_per_solution: int = 2
    max_pvwatts_sites: int = 5
    # Live Grants.gov listings shown per opportunity.
    max_grant_leads: int = 3
    # Some counties have thousands of RE-Powering sites or chargers. The map
    # gets the largest sites first, up to these limits; counts are still exact.
    max_sites_per_county: int = 150
    max_ev_markers_per_county: int = 300
    max_buildings_per_county: int = 300
    # Plants that could be repowered come first, then the largest.
    max_power_plants_per_county: int = 100

    # --- peer counties (peers.py) ------------------------------------------
    peer_count: int = 5

    # --- Gemini-written opportunities (opportunities.py) ------------------
    # Sites of each type listed in the digest sent to Gemini, best first. The
    # digest gives the exact count for the rest.
    digest_sites_per_type: int = 3

    # --- highlights (highlights.py) -----------------------------------------
    # A factor is a highlight only if the county is at least this many times
    # better than the benchmark. Our own cut-off, the same as need_ratio_high.
    highlight_min_ratio: float = 1.25
    max_highlights: int = 3

    # --- Site Scout (app/scout/) ---------------------------------------------
    # Counties the sponsor's building data covers: Harris County, TX and Los
    # Angeles County, CA. VERIFY the real coverage before a demo.
    scout_counties: list[str] = ["48201", "06037"]
    # Hard limits on one run, so a demo cannot run away.
    scout_max_actions: int = 25
    # Roofs inspected (each is a parcel record plus a live detection call).
    scout_max_roofs: int = 8
    scout_target_sites: int = 5
    scout_draft_sites: int = 3
    scout_search_limit: int = 10
    # Half the side of the square the imagery search covers, in metres.
    scout_search_half_m: float = 3000.0
    # Live detection and imagery calls take several seconds each.
    scout_detect_timeout_s: float = 60.0
    # Pause between steps when a saved run is replayed (?fixture=1).
    scout_fixture_step_delay_s: float = 0.3
    # Cross-layer check: the scan's floor area may be smaller than the aerial
    # footprint by this share (walls, unscanned rooms) before the two layers
    # are said to disagree. Our own cut-off.
    scout_footprint_tolerance: float = 0.3
    # Confidence factors on a site's score. Our own choices.
    scout_factor_no_scan: float = 0.85
    scout_factor_low_confidence: float = 0.7
    scout_factor_footprint_mismatch: float = 0.6

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
