"""The API contract. Change this file first, then mirror it in
frontend/src/types.ts. Do not let the two drift."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

# --- request ---------------------------------------------------------------

LonLat = tuple[float, float]


class AnalyzeCountyRequest(BaseModel):
    mode: Literal["county"]
    fips: str = Field(pattern=r"^\d{5}$")


class AnalyzeBboxRequest(BaseModel):
    mode: Literal["bbox"]
    sw: LonLat  # [lon, lat]
    ne: LonLat  # [lon, lat]


AnalyzeRequest = Annotated[AnalyzeCountyRequest | AnalyzeBboxRequest, Field(discriminator="mode")]

# --- response --------------------------------------------------------------

NeedId = Literal[
    "low_income",
    "old_housing",
    "delivered_fuel_heat",
    "mobile_homes",
    "renters",
    "no_vehicle",
    "long_commute",
    "drive_alone",
    "ev_charging_gap",
    "landfill_gas_uncaptured",
    "hazard_risk",
    "high_electricity_cost",  # state average, EIA
    "carbon_intensive_grid",  # state average, EPA eGRID
    "unmanaged_farm_waste",  # large livestock farms without digesters
    "urban_heat",  # built-up land much hotter than the county's natural land (Landsat)
    "low_tree_canopy",  # little tree canopy on the county's built-up land (USFS TCC)
]
NeedTheme = Literal["equity", "energy", "transportation", "waste", "resilience"]
NeedLevel = Literal["elevated", "high"]
NeedUnit = Literal[
    "share",
    "usd",
    "per_10k_residents",
    "count",
    "rating",
    "usd_per_month",
    "lb_co2_per_mwh",
    "celsius",  # a temperature difference in degrees Celsius
]

AssetType = Literal[
    "brownfield",  # EPA or state brownfields program site
    "contaminated_site",  # Superfund, RCRA or state cleanup site
    "landfill",  # RE-Powering "known landfill"; open or closed is not recorded
    "mine_land",  # RE-Powering "known abandoned mine land"
    "lmop_landfill",  # landfill in the EPA LMOP database
    "school",
    "library",
    "college",
    "ev_station",
    "non_powered_dam",  # dam in ORNL's hydropower potential assessment
]

# What kind of thing a factor is. "workforce" is the county's local talent.
FactorCategory = Literal[
    "climate",
    "energy",
    "land",
    "buildings",
    "workforce",
    "economy",
    "infrastructure",
    "agriculture",
]

FundingStatus = Literal["open", "open_with_deadlines", "paused", "uncertain", "unverified"]

AttributeValue = float | int | str | bool | None


class Need(BaseModel):
    id: NeedId
    label: str
    theme: NeedTheme
    # Null when the need is categorical (see `detail`).
    value: float | None
    # Null when there is no national figure to compare against.
    national_value: float | None
    unit: NeedUnit
    severity: int = Field(ge=0, le=100)
    # Severity scaled by how much the need counts for sustainability
    # (the need_priority_* weights in config). Needs are ordered by it and opportunity
    # scores are built from it. Equal to severity until the pipeline sets it,
    # and for reports saved before it existed.
    priority: int = Field(ge=0, le=100)
    level: NeedLevel
    source: str
    # Plain-language specifics, e.g. which hazard is rated high.
    detail: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _priority_defaults_to_severity(cls, data: Any) -> Any:
        if isinstance(data, dict) and "priority" not in data:
            return {**data, "priority": data.get("severity")}
        return data


class Asset(BaseModel):
    id: str
    type: AssetType
    name: str
    lat: float
    lon: float
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)
    source: str


class Factor(BaseModel):
    """One fact about the county that a solution could draw on: its climate,
    land, buildings, workforce, economy, energy system, infrastructure or
    farms. Together with the mapped assets, the factors are everything Gemini
    is given to build opportunities from. Values are display-ready text so the
    same wording reaches the model and the panel."""

    id: str
    category: FactorCategory
    label: str
    value: str
    # The same figure for the US, where there is one.
    comparison: str | None = None
    detail: str | None = None
    source: str


class Resources(BaseModel):
    solar_kwh_per_kw_year: float | None = None
    # USDA Census of Agriculture 2022. Null where the census withholds it.
    land_in_farms_acres: float | None = None
    cropland_acres: float | None = None


class Estimate(BaseModel):
    label: str
    value: float
    unit: str
    # Every computed figure is an estimate and says so.
    is_estimate: Literal[True] = True
    assumptions: list[str]


class FundingMatch(BaseModel):
    program_id: str
    name: str
    agency: str
    status: FundingStatus
    deadline: str | None
    key_terms: str
    verified_on: str | None
    source_url: str | None


class Opportunity(BaseModel):
    """A way to meet one or more needs with what the county has.

    Written by Gemini from the needs, factors and sites ("gemini"), or, when
    Gemini is unavailable or its answer is rejected, matched by the rules in
    data/solutions.json ("rules"). Either way code supplies the score, the
    estimates and the funding leads; Gemini writes none of them.
    """

    id: str
    # A project type from data/solutions.json. Null for an idea of Gemini's that
    # fits none of them; only a typed opportunity can get estimates and funding.
    solution_id: str | None
    title: str
    # What would be done, and why it fits this county. Null for a rule match.
    summary: str | None = None
    rationale: str | None = None
    first_steps: list[str] = Field(default_factory=list)
    need_ids: list[NeedId]
    # The mapped sites it would use, the main one first. Empty for a county-wide
    # opportunity.
    asset_ids: list[str] = Field(default_factory=list)
    # The factors it draws on.
    factor_ids: list[str] = Field(default_factory=list)
    score: int = Field(ge=0, le=100)
    estimates: list[Estimate]
    funding: list[FundingMatch]
    generated_by: Literal["gemini", "rules"]


class OpportunityNote(BaseModel):
    opportunity_id: str
    text: str


class Narrative(BaseModel):
    summary: str
    # Only the template writes these; a Gemini opportunity carries its own text.
    opportunity_notes: list[OpportunityNote]
    caveats: list[str]
    generated_by: Literal["gemini", "template"]


class SourceStatus(BaseModel):
    source: str
    ok: bool
    cached: bool
    message: str | None = None


class CountyContext(BaseModel):
    """Figures shown for context only. They never trigger a need."""

    median_household_income: float | None = None
    poverty_rate: float | None = None
    unemployment_rate: float | None = None
    electric_heat_share: float | None = None
    # State averages; not published by county.
    state_electricity_price_cents_kwh: float | None = None
    state_grid_co2_lb_per_mwh: float | None = None


class CountyReport(BaseModel):
    fips: str
    name: str
    state: str
    population: int | None
    context: CountyContext
    needs: list[Need]
    assets: list[Asset]
    # Exact number of assets found per type, before the map caps in config.
    asset_counts: dict[str, int] = Field(default_factory=dict)
    # Asset types searched for and not found, so the panel can say so.
    missing_asset_types: list[AssetType] = Field(default_factory=list)
    resources: Resources
    # Everything known about the county that a solution could draw on.
    factors: list[Factor] = Field(default_factory=list)
    # Written by Gemini from the needs, factors and assets above. The `gemini`
    # entry in `source_status` says why when they are rule matches instead.
    opportunities: list[Opportunity]
    narrative: Narrative
    source_status: list[SourceStatus]


class AnalyzeResponse(BaseModel):
    counties: list[CountyReport]
    truncated: bool = False
    message: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    counties_loaded: int
    data_files: dict[str, bool]
    keys: dict[str, bool]
