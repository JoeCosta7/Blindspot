"""The API contract. Change this file first, then mirror it in
frontend/src/types.ts. Do not let the two drift."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

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
]
NeedTheme = Literal["equity", "energy", "transportation", "waste", "resilience"]
NeedLevel = Literal["elevated", "high"]
NeedUnit = Literal["share", "usd", "per_10k_residents", "count", "rating"]

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
    level: NeedLevel
    source: str
    # Plain-language specifics, e.g. which hazard is rated high.
    detail: str | None = None


class Asset(BaseModel):
    id: str
    type: AssetType
    name: str
    lat: float
    lon: float
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)
    source: str


class Resources(BaseModel):
    solar_kwh_per_kw_year: float | None = None


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
    id: str
    solution_id: str
    title: str
    asset_id: str
    need_ids: list[NeedId]
    score: int = Field(ge=0, le=100)
    estimates: list[Estimate]
    funding: list[FundingMatch]


class OpportunityNote(BaseModel):
    opportunity_id: str
    text: str


class Narrative(BaseModel):
    summary: str
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
