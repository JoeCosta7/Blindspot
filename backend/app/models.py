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
    "high_energy_burden",  # low-income households' energy bills against income (DOE LEAD)
    "utility_shutoff_risk",  # adults threatened with a utility shut-off (CDC PLACES)
    "unsafe_drinking_water",  # community water systems with health-based violations (EPA ECHO)
    "impaired_waters",  # assessed streams and lakes not meeting their uses (EPA ATTAINS)
    "poor_air_quality",  # PM2.5 or ozone at or near the national standard (EPA design values)
]
NeedTheme = Literal["equity", "energy", "transportation", "waste", "resilience", "water", "health"]
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
    "ug_m3",  # micrograms per cubic meter (PM2.5)
    "ppm",  # parts per million (ozone)
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
    "power_plant",  # plant of 1 MW or more in EIA-860, operating or retired
    "commercial_building",  # a property found by Site Scout (scout/), shown as a map marker
]

# What kind of thing a factor is. "workforce" is the county's local talent.
FactorCategory = Literal[
    "climate",
    "energy",
    "land",
    "buildings",
    "workforce",
    "economy",
    "community",
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


ProfileCategory = Literal["workforce", "economy", "community", "climate", "geography"]
ProfileUnit = Literal[
    "share",
    "years",
    "degree_days_f",  # per year, against 65 F
    "days_per_year",
    "inches_per_year",
    "m_per_s",
    "percentile",  # 0-1 rank among US counties
    "feet",
    "degrees",
]


class ProfileFact(BaseModel):
    """A measured figure about the county's workforce, industries, community,
    climate or land, as profile.py builds it. Not sent to the browser as is:
    factors.py turns each one into a `Factor`, and the report carries those."""

    id: str
    category: ProfileCategory
    label: str
    value: float
    # Null when there is no national figure to compare against.
    national_value: float | None = None
    unit: ProfileUnit
    source: str
    # Plain-language specifics, e.g. what an occupation group includes.
    note: str | None = None


class Factor(BaseModel):
    """One fact about the county that a solution could draw on: its climate,
    land, buildings, workforce, economy, community, energy system,
    infrastructure or farms. Together with the mapped assets, the factors are
    everything Gemini is given to build opportunities from. Values are
    display-ready text so the same wording reaches the model and the panel."""

    id: str
    category: FactorCategory
    label: str
    value: str
    # The same figure for the US, where there is one.
    comparison: str | None = None
    detail: str | None = None
    source: str


class Highlight(BaseModel):
    """A factor where the county stands out for the better: it beats the US
    figure, or has far more of some land than the average US county. Chosen
    and worded by code (highlights.py), not by Gemini."""

    factor_id: str
    label: str
    value: str  # the factor's own display text
    benchmark: str  # e.g. "US: 8.5%" or "Average US county: 290 acres"
    # How many times better than the benchmark, rounded to one decimal.
    times_better: float
    why: str


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
    # Why the program applies here, when that depends on the county or site,
    # e.g. which energy community category the county falls in.
    note: str | None = None
    # What kind of money: only a grant has an application deadline. A tax
    # credit's date is a placed-in-service date, never an application date.
    kind: Literal["grant", "tax_credit", "other"] = "other"
    # Whether a county government may apply, from the curated table's
    # eligible applicants; "unknown" for live listings we have not read.
    county_eligible: Literal["yes", "no", "unknown"] = "unknown"


DocumentKind = Literal[
    "grant_application",
    "program_guide",
    "tax_form",
    "property_agreement",
    "environmental_review",
    "interconnection",
    "other",
]


class ProjectDocument(BaseModel):
    """A form or packet the county may need. Curated by code (with a real URL
    when we have one) or named by Gemini as something to gather locally
    (source_url null). Gemini never invents URLs."""

    id: str
    name: str
    kind: DocumentKind
    description: str
    agency: str | None = None
    source_url: str | None = None
    verified_on: str | None = None


class SiteActionPlan(BaseModel):
    """How to carry out the opportunity at one named site.

    Written by Gemini from facts about that site (owner type, cleanup status,
    gas-project candidate, and so on). Not legal advice: compliance lines are
    checks to make with the right agency, not citations of local law.
    """

    site_id: str
    # Start-to-finish course of action at this site.
    course_of_action: list[str] = Field(default_factory=list)
    # Agency and ownership checks that follow from known site facts.
    compliance: list[str] = Field(default_factory=list)
    # Forms and agreements to gather for this site (names only; no invented URLs).
    documents_needed: list[str] = Field(default_factory=list)


class NeedFit(BaseModel):
    """How an opportunity reduces one of the needs it addresses, in Gemini's
    words. Code decides which needs count: one Gemini did not explain, or one
    the project type is not catalogued to answer, is not in need_ids at all."""

    need_id: NeedId
    text: str


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
    # One plain sentence on what the project is, from the catalogue.
    description: str | None = None
    # What would be done, and why it fits this county. Null for a rule match.
    summary: str | None = None
    rationale: str | None = None
    # County-wide course of action (start to finish). Also kept as first_steps
    # for older callers; the two are the same list when Gemini wrote them.
    plan: list[str] = Field(default_factory=list)
    first_steps: list[str] = Field(default_factory=list)
    # County-level compliance checks (permits, agencies, eligibility).
    compliance: list[str] = Field(default_factory=list)
    need_ids: list[NeedId]
    # One per need in need_ids when Gemini wrote the opportunity: how the
    # project reduces that need. Empty for a rule match.
    need_fits: list[NeedFit] = Field(default_factory=list)
    # The mapped sites it would use, the main one first. Empty for a county-wide
    # opportunity.
    asset_ids: list[str] = Field(default_factory=list)
    # Per-site course of action and compliance. Empty when county-wide or when
    # Gemini did not write one for a named site.
    site_plans: list[SiteActionPlan] = Field(default_factory=list)
    # The factors it draws on.
    factor_ids: list[str] = Field(default_factory=list)
    score: int = Field(ge=0, le=100)
    estimates: list[Estimate]
    # The curated table's matches plus live Grants.gov listings (program_id
    # "grantsgov:<id>", found by the searches in data/grants_queries.json).
    funding: list[FundingMatch]
    # Grant packets and guides matched by code, plus application pages from
    # funding leads. Never invented by Gemini.
    documents: list[ProjectDocument] = Field(default_factory=list)
    # Forms to gather that have no single national URL (leases, local permits).
    # Named by Gemini from site facts; shown as a checklist.
    documents_needed: list[str] = Field(default_factory=list)
    generated_by: Literal["gemini", "rules"]
    # Site Scout can look for a building to host this: the county has building
    # data, the opportunity names a site (it is not county-wide), and a
    # building-hosted project type answers one of its needs.
    scoutable: bool = False

    @model_validator(mode="before")
    @classmethod
    def _plan_defaults_from_first_steps(cls, data: Any) -> Any:
        """Reports saved before `plan` existed still open: copy first_steps."""
        if isinstance(data, dict) and "plan" not in data and data.get("first_steps"):
            return {**data, "plan": data["first_steps"]}
        return data


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


class PeerNeed(BaseModel):
    id: NeedId
    label: str
    # Whether the county the report is about has this need flagged too.
    shared: bool


class PeerCounty(BaseModel):
    """A county of similar size, income and density (peers.py). Listed so an
    official can see how a place like theirs compares; clicking one analyzes it."""

    fips: str
    name: str
    state: str
    population: int
    median_household_income: float
    people_per_sq_mile: float
    # The peer's flagged Census needs, scored the same way as the report's own.
    # Only Census needs: the others are not measured for every county up front.
    needs: list[PeerNeed]


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
    # The few factors that stand out most for the better, best first.
    highlights: list[Highlight] = Field(default_factory=list)
    # Written by Gemini from the needs, factors and assets above. The `gemini`
    # entry in `source_status` says why when they are rule matches instead.
    opportunities: list[Opportunity]
    # The most similar counties, closest first. Empty when the all-county
    # Census figures did not load (the `peers` entry in `source_status`).
    peers: list[PeerCounty] = Field(default_factory=list)
    # This county's own density, to read beside the peers'.
    people_per_sq_mile: float | None = None
    narrative: Narrative
    source_status: list[SourceStatus]
    # True for the counties Site Scout has building data for (settings.scout_counties).
    scout_available: bool = False


class NeedMapLayer(BaseModel):
    """One Census need for every county, for coloring the map."""

    id: NeedId
    label: str
    theme: NeedTheme
    unit: NeedUnit
    national_value: float
    # True where a lower figure is the worse one (income).
    lower_is_worse: bool
    # County FIPS -> [value, ratio]. The ratio is county against national,
    # turned so that above 1 always means worse off, exactly as needs are
    # scored. Counties the Census publishes no figure for are left out.
    counties: dict[str, tuple[float, float]]


class NeedMapResponse(BaseModel):
    layers: list[NeedMapLayer]
    # Ratio thresholds between the map's color classes, ascending: the US
    # figure itself, the "elevated" and "high" need thresholds, and the ratio
    # at which severity reaches 100.
    ratio_breaks: list[float]
    source: str


class AnalyzeResponse(BaseModel):
    counties: list[CountyReport]
    truncated: bool = False
    message: str | None = None


class AnalyzeEvent(BaseModel):
    """One line of the NDJSON stream from POST /api/analyze?progress=1.

    `source` events arrive as each data fetch finishes; `done` carries the
    full response last. `error` is used when a county cannot be analyzed.
    """

    type: Literal["source", "done", "error"]
    fips: str | None = None
    status: SourceStatus | None = None
    response: AnalyzeResponse | None = None
    message: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    counties_loaded: int
    data_files: dict[str, bool]
    keys: dict[str, bool]


# --- Site Scout (app/scout/) -----------------------------------------------------
# A building-level agent for the counties the sponsor's data covers. The
# county report says what is needed; the scout finds specific commercial
# buildings to host a fix and checks three data layers against each other.

ScoutLayer = Literal["vexcel", "matterport", "costar"]
SiteStatus = Literal["shortlisted", "low_confidence", "rejected"]
ScoutStepKind = Literal["plan", "action", "observation", "decision", "error"]
ScoutStop = Literal["target_reached", "budget_spent", "no_more_candidates", "error"]


class ScoutRequest(BaseModel):
    fips: str = Field(pattern=r"^\d{5}$")
    # Scope the run to one opportunity: only these needs count, and the plan
    # names the opportunity. Empty means every flagged need.
    need_ids: list[NeedId] = Field(default_factory=list)
    focus: str | None = None  # the opportunity's title, for the timeline
    # The opportunity's project type, which decides what kind of host to look
    # for first (agent.profile_for). Null for an idea of Gemini's own.
    solution_id: str | None = None
    # What the project is (catalogue description or Gemini's summary), for the host spec.
    detail: str | None = None
    # The opportunity's own sites (report asset ids), tried first for site projects.
    asset_ids: list[str] = Field(default_factory=list)


class Observation(BaseModel):
    """Something a data layer or the vision model reported. Not computed by us,
    and kept apart from estimates: a count of parked cars is what one image
    showed on one day, not a figure derived from it."""

    label: str  # "Parked cars", "Roof type", "Existing solar"
    value: bool | float | str
    unit: str | None = None
    layer: ScoutLayer
    # The image's capture date or the record's date, when the layer gives one.
    captured: str | None = None
    # True when the vision model read it off an image rather than a record.
    by_model: bool = False


class LayerCheck(BaseModel):
    """One layer compared with another, e.g. the roof area measured in the
    aerial image against the footprint the property record gives."""

    label: str
    layers: list[ScoutLayer]
    ok: bool
    detail: str  # code-written, e.g. "11,130 m² in the image vs 11,148 m² on record"


class GeoOverlay(BaseModel):
    """A vendor polygon in map coordinates: the parcel, the building
    footprint or a detected object. Drawn on the aerial image by the imagery
    server and available here for the map."""

    label: str  # "Parcel", "Footprint", "Rooftop HVAC unit", "Solar panel", "Car"
    coordinates: list[LonLat]


class ImageOption(BaseModel):
    label: str  # "From the south"
    url: str


class ScanRoom(BaseModel):
    """One room of a Matterport 3D scan, as the API reports it."""

    label: str | None
    area_m2: float | None
    ceiling_m: float | None


class SiteCandidate(BaseModel):
    building_id: str
    name: str
    address: str | None
    lat: float
    lon: float
    # The property record's building type, or the scan's classification.
    property_type: str | None
    status: SiteStatus
    # Project types from data/scout_solutions.json the building could host.
    solution_ids: list[str]
    need_ids: list[NeedId]
    layers_used: list[ScoutLayer]
    observations: list[Observation]
    checks: list[LayerCheck]
    estimates: list[Estimate]
    funding: list[FundingMatch]
    # Code-written warnings, e.g. a ground-floor electrical room in a flood-prone county.
    flags: list[str]
    # Why the status is what it is, written by code.
    status_reason: str
    # The agent's own reasoning about this building, number-checked. Null when
    # it failed the check or the rule fallback chose the building.
    agent_note: str | None
    score: int = Field(ge=0, le=100)
    # Served by this backend (the image is fetched and cached server-side).
    image_url: str | None
    # A side-angle photo where the urban layer has one, else the top-down view.
    image_view: Literal["oblique", "ortho"] | None = None
    # Every available side-angle view, fetched on first request (main.py).
    image_options: list[ImageOption] = Field(default_factory=list)
    overlays: list[GeoOverlay]
    # The scan's own AI-written summary of the interior, quoted as is.
    interior_summary: str | None
    # Rooms read from the Matterport API: largest first.
    scan_rooms: list[ScanRoom] = Field(default_factory=list)
    scan_floors: int | None = None
    scan_image_url: str | None = None
    # The public Matterport tour page, when the scan is published.
    tour_url: str | None


class SiteDrafts(BaseModel):
    building_id: str
    outreach_letter: str
    grant_paragraph: str
    generated_by: Literal["gemini", "template"]


class ScoutStep(BaseModel):
    index: int
    kind: ScoutStepKind
    text: str  # one line for the timeline
    tool: str | None = None
    building_id: str | None = None
    # Where the step happened, so the map can fly there as it plays.
    lat: float | None = None
    lon: float | None = None


class ScoutResult(BaseModel):
    fips: str
    # Shortlisted first, then low confidence, then rejected; best score first within each.
    candidates: list[SiteCandidate]
    drafts: list[SiteDrafts]
    steps: list[ScoutStep]
    stopped_because: ScoutStop
    source_status: list[SourceStatus]
    # Who chose the actions: Gemini, the rule fallback, or a saved run.
    generated_by: Literal["gemini", "rules", "fixture"]
    # "fake" is hand-written placeholder data (data/scout_fake/); say so in the UI.
    provider: Literal["fake", "real"]


class ScoutEvent(BaseModel):
    """One line of the NDJSON stream from POST /api/scout. The last is a result."""

    type: Literal["step", "candidate", "result"]
    step: ScoutStep | None = None
    candidate: SiteCandidate | None = None
    result: ScoutResult | None = None


# --- Opportunity workspace: the government process (app/process.py) ------------------

ProcessStepSource = Literal["template", "site_plan", "plan"]


class ProcessStep(BaseModel):
    """One thing the county does to move a project forward. From the curated
    table (data/process_templates.json), the chosen site's plan, or the
    opportunity's own plan. General practice, not legal advice."""

    id: str
    title: str
    detail: str | None = None
    # Who usually does it, e.g. "County board" or "Planning department".
    who: str | None = None
    # Ids of documents in ProcessPlan.documents this step uses.
    document_ids: list[str] = Field(default_factory=list)
    # Ids of ProcessPlan.sections (drafts) this step uses, e.g. the resolution.
    section_ids: list[str] = Field(default_factory=list)
    # Suggested do-by date (ISO), counted back from the target grant's deadline
    # by the step's lead time, which is our own planning assumption. Only the
    # submission step's date is the program's real deadline.
    due: str | None = None
    # The suggested date has passed (behind the suggested schedule).
    late: bool = False
    # common_practice: general county practice, a suggestion. program_rule: a
    # requirement of the program, with its source.
    basis: Literal["common_practice", "program_rule"] = "common_practice"
    source_url: str | None = None
    source: ProcessStepSource


class ProcessPhase(BaseModel):
    id: str
    title: str
    steps: list[ProcessStep]


class PackSection(BaseModel):
    """One part of the printable document pack. Code fills it from the
    opportunity's own figures; Gemini may reword it (number-checked)."""

    id: str
    title: str
    text: str
    generated_by: Literal["gemini", "template"]


class ProcessPlan(BaseModel):
    opportunity_id: str
    site_id: str | None
    # The grant the dates count back from, and the dated leads one could pick.
    target: FundingMatch | None = None
    targets: list[FundingMatch] = Field(default_factory=list)
    today: str | None = None
    phases: list[ProcessPhase]
    # Lines from the chosen site's plan or the opportunity's own plan: shown
    # apart from the checklist because they often repeat its steps.
    ideas: list[ProcessStep] = Field(default_factory=list)
    documents: list[ProjectDocument]
    # Forms to gather locally that have no national URL.
    documents_needed: list[str]
    sections: list[PackSection]
    caveats: list[str]


class ProcessRequest(BaseModel):
    """The client sends what it already has; nothing is re-analyzed."""

    fips: str = Field(pattern=r"^\d{5}$")
    county_name: str
    state: str
    opportunity: Opportunity
    # The needs the opportunity addresses, for the pack's wording.
    needs: list[Need] = Field(default_factory=list)
    # The chosen site: a Site Scout candidate, or one of the report's own sites.
    site: SiteCandidate | None = None
    site_asset: Asset | None = None
    # The funding lead to aim for; empty means the earliest open deadline.
    target_program_id: str | None = None
