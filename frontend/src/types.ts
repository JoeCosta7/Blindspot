// Mirror of backend/app/models.py, which is the API contract.
// Change models.py first, then this file. Do not let them drift.

export type LonLat = [number, number];

export type AnalyzeRequest =
  | { mode: "county"; fips: string }
  | { mode: "bbox"; sw: LonLat; ne: LonLat };

export type NeedId =
  | "low_income"
  | "old_housing"
  | "delivered_fuel_heat"
  | "mobile_homes"
  | "renters"
  | "no_vehicle"
  | "long_commute"
  | "drive_alone"
  | "ev_charging_gap"
  | "landfill_gas_uncaptured"
  | "hazard_risk"
  | "high_electricity_cost"
  | "carbon_intensive_grid"
  | "unmanaged_farm_waste"
  | "urban_heat"
  | "low_tree_canopy"
  | "high_energy_burden"
  | "utility_shutoff_risk"
  | "unsafe_drinking_water"
  | "impaired_waters"
  | "poor_air_quality";

export type NeedTheme =
  | "equity"
  | "energy"
  | "transportation"
  | "waste"
  | "resilience"
  | "water"
  | "health";
export type NeedLevel = "elevated" | "high";
export type NeedUnit =
  | "share"
  | "usd"
  | "per_10k_residents"
  | "count"
  | "rating"
  | "usd_per_month"
  | "lb_co2_per_mwh"
  | "celsius"
  | "ug_m3"
  | "ppm";

export type AssetType =
  | "brownfield"
  | "contaminated_site"
  | "landfill"
  | "mine_land"
  | "lmop_landfill"
  | "school"
  | "library"
  | "college"
  | "ev_station"
  | "non_powered_dam"
  | "power_plant"
  /** A building found by Site Scout; drawn as a marker. */
  | "commercial_building";

export type FundingStatus =
  | "open"
  | "open_with_deadlines"
  | "paused"
  | "uncertain"
  | "unverified";

export type AttributeValue = number | string | boolean | null;

export interface Need {
  id: NeedId;
  label: string;
  theme: NeedTheme;
  /** Null when the need is categorical (see `detail`). */
  value: number | null;
  /** Null when there is no national figure to compare against. */
  national_value: number | null;
  unit: NeedUnit;
  severity: number;
  /** Severity weighted by how much the need's theme counts for sustainability.
   * The backend orders needs by it and builds opportunity scores from it. */
  priority: number;
  level: NeedLevel;
  source: string;
  detail: string | null;
}

export interface Asset {
  id: string;
  type: AssetType;
  name: string;
  lat: number;
  lon: number;
  attributes: Record<string, AttributeValue>;
  source: string;
}

export type FactorCategory =
  | "climate"
  | "energy"
  | "land"
  | "buildings"
  | "workforce"
  | "economy"
  | "community"
  | "infrastructure"
  | "agriculture";

/** One fact about the county that a solution could draw on. Values are
 * display-ready text. */
export interface Factor {
  id: string;
  category: FactorCategory;
  label: string;
  value: string;
  /** The same figure for the US, where there is one. */
  comparison: string | null;
  detail: string | null;
  source: string;
}

/** A factor where the county stands out for the better. Chosen by code. */
export interface Highlight {
  factor_id: string;
  label: string;
  value: string;
  /** e.g. "US: 8.5%" or "Average US county: 290 acres" */
  benchmark: string;
  /** How many times better than the benchmark, one decimal. */
  times_better: number;
  why: string;
}

export interface Resources {
  solar_kwh_per_kw_year: number | null;
  /** USDA Census of Agriculture 2022. Null where the census withholds it. */
  land_in_farms_acres: number | null;
  cropland_acres: number | null;
}

export interface Estimate {
  label: string;
  value: number;
  unit: string;
  is_estimate: true;
  assumptions: string[];
}

export interface FundingMatch {
  program_id: string;
  name: string;
  agency: string;
  status: FundingStatus;
  deadline: string | null;
  key_terms: string;
  verified_on: string | null;
  source_url: string | null;
  /** Why the program applies here, when that depends on the county or site. */
  note: string | null;
}


export type DocumentKind =
  | "grant_application"
  | "program_guide"
  | "tax_form"
  | "property_agreement"
  | "environmental_review"
  | "interconnection"
  | "other";

/** A form or packet the county may need. Curated by code (with a real URL when
 * we have one) or named by Gemini as something to gather locally (source_url
 * null). Gemini never invents URLs. */
export interface ProjectDocument {
  id: string;
  name: string;
  kind: DocumentKind;
  description: string;
  agency: string | null;
  source_url: string | null;
  verified_on: string | null;
}

/** How to carry out the opportunity at one named site. */
export interface SiteActionPlan {
  site_id: string;
  /** Start-to-finish course of action at this site. */
  course_of_action: string[];
  /** Agency and ownership checks from known site facts — not legal advice. */
  compliance: string[];
  /** Forms and agreements to gather for this site (names only). */
  documents_needed: string[];
}

/** A way to meet one or more needs with what the county has. Written by Gemini
 * from the needs, factors and sites, or matched by rule when Gemini is
 * unavailable. Code supplies the score, estimates, funding and document links. */
export interface Opportunity {
  id: string;
  /** A catalogued project type. Null for an idea of Gemini's that fits none;
   * only a typed opportunity can get estimates and funding. */
  solution_id: string | null;
  title: string;
  /** What would be done, and why it fits this county. Null for a rule match. */
  summary: string | null;
  rationale: string | null;
  /** County-wide start-to-finish course of action. */
  plan: string[];
  /** Same list as plan for older callers. */
  first_steps: string[];
  /** County-level compliance checks. */
  compliance: string[];
  need_ids: NeedId[];
  /** The mapped sites it would use, the main one first. Empty when county-wide. */
  asset_ids: string[];
  /** Per-site course of action and compliance. */
  site_plans: SiteActionPlan[];
  /** The factors it draws on. */
  factor_ids: string[];
  score: number;
  estimates: Estimate[];
  /** The curated table's matches plus live Grants.gov listings
   * (program_id "grantsgov:<id>"). */
  funding: FundingMatch[];
  /** Grant packets and guides matched by code, plus application pages from
   * funding leads. Never invented by Gemini. */
  documents: ProjectDocument[];
  /** Forms to gather that have no single national URL. Named by Gemini. */
  documents_needed: string[];
  generated_by: "gemini" | "rules";
  /** Site Scout can look for a building to host this: it names a site and a
   * building-hosted project type answers one of its needs. */
  scoutable: boolean;
}

export interface OpportunityNote {
  opportunity_id: string;
  text: string;
}

export interface Narrative {
  summary: string;
  /** Only the template writes these; a Gemini opportunity carries its own text. */
  opportunity_notes: OpportunityNote[];
  caveats: string[];
  generated_by: "gemini" | "template";
}

export interface SourceStatus {
  source: string;
  ok: boolean;
  cached: boolean;
  message: string | null;
}

/** Figures shown for context only. They never trigger a need. */
export interface CountyContext {
  median_household_income: number | null;
  poverty_rate: number | null;
  unemployment_rate: number | null;
  electric_heat_share: number | null;
  /** State averages; not published by county. */
  state_electricity_price_cents_kwh: number | null;
  state_grid_co2_lb_per_mwh: number | null;
}

export interface PeerNeed {
  id: NeedId;
  label: string;
  /** Whether the county the report is about has this need flagged too. */
  shared: boolean;
}

/** A county of similar size, income and density. */
export interface PeerCounty {
  fips: string;
  name: string;
  state: string;
  population: number;
  median_household_income: number;
  people_per_sq_mile: number;
  /** The peer's flagged Census needs, scored like the report's own. */
  needs: PeerNeed[];
}

export interface CountyReport {
  fips: string;
  name: string;
  state: string;
  population: number | null;
  context: CountyContext;
  needs: Need[];
  assets: Asset[];
  /** Exact number of assets found per type, before the map caps. */
  asset_counts: Record<string, number>;
  /** Asset types searched for and not found. */
  missing_asset_types: AssetType[];
  resources: Resources;
  /** Everything known about the county that a solution could draw on. */
  factors: Factor[];
  /** The few factors that stand out most for the better, best first. */
  highlights: Highlight[];
  /** Written by Gemini from the needs, factors and assets. The `gemini` entry
   * in `source_status` says why when they are rule matches instead. */
  opportunities: Opportunity[];
  /** The most similar counties, closest first. Empty when the all-county
   * Census figures did not load (the `peers` entry in `source_status`). */
  peers: PeerCounty[];
  /** This county's own density, to read beside the peers'. */
  people_per_sq_mile: number | null;
  narrative: Narrative;
  source_status: SourceStatus[];
  /** True for the counties Site Scout has building data for. */
  scout_available: boolean;
}

// --- Site Scout --------------------------------------------------------------

export type ScoutLayer = "vexcel" | "matterport" | "costar";
export type SiteStatus = "shortlisted" | "low_confidence" | "rejected";
export type ScoutStepKind = "plan" | "action" | "observation" | "decision" | "error";
export type ScoutStop = "target_reached" | "budget_spent" | "no_more_candidates" | "error";

/** Something a data layer or a model reported; not computed by us. */
export interface Observation {
  label: string;
  value: boolean | number | string;
  unit: string | null;
  layer: ScoutLayer;
  captured: string | null;
  by_model: boolean;
}

/** One layer compared with another. */
export interface LayerCheck {
  label: string;
  layers: ScoutLayer[];
  ok: boolean;
  detail: string;
}

export interface GeoOverlay {
  label: string;
  coordinates: LonLat[];
}

export interface ImageOption {
  label: string;
  url: string;
}

export interface SiteCandidate {
  building_id: string;
  name: string;
  address: string | null;
  lat: number;
  lon: number;
  property_type: string | null;
  status: SiteStatus;
  solution_ids: string[];
  need_ids: NeedId[];
  layers_used: ScoutLayer[];
  observations: Observation[];
  checks: LayerCheck[];
  estimates: Estimate[];
  funding: FundingMatch[];
  flags: string[];
  status_reason: string;
  agent_note: string | null;
  score: number;
  image_url: string | null;
  /** A side-angle photo where the urban layer has one, else top-down. */
  image_view: "oblique" | "ortho" | null;
  /** Every available side-angle view, fetched on first request. */
  image_options: ImageOption[];
  overlays: GeoOverlay[];
  interior_summary: string | null;
  tour_url: string | null;
}

export interface SiteDrafts {
  building_id: string;
  outreach_letter: string;
  grant_paragraph: string;
  generated_by: "gemini" | "template";
}

export interface ScoutStep {
  index: number;
  kind: ScoutStepKind;
  text: string;
  tool: string | null;
  building_id: string | null;
  lat: number | null;
  lon: number | null;
}

export interface ScoutResult {
  fips: string;
  candidates: SiteCandidate[];
  drafts: SiteDrafts[];
  steps: ScoutStep[];
  stopped_because: ScoutStop;
  source_status: SourceStatus[];
  generated_by: "gemini" | "rules" | "fixture";
  provider: "fake" | "real";
}

/** One line of the NDJSON stream from POST /api/scout. */
export interface ScoutEvent {
  type: "step" | "candidate" | "result";
  step: ScoutStep | null;
  candidate: SiteCandidate | null;
  result: ScoutResult | null;
}

/** One Census need for every county, for coloring the map. */
export interface NeedMapLayer {
  id: NeedId;
  label: string;
  theme: NeedTheme;
  unit: NeedUnit;
  national_value: number;
  /** True where a lower figure is the worse one (income). */
  lower_is_worse: boolean;
  /** County FIPS -> [value, ratio]. The ratio is county against national,
   * turned so that above 1 always means worse off, as needs are scored.
   * Counties with no published figure are left out. */
  counties: Record<string, [number, number]>;
}

export interface NeedMapResponse {
  layers: NeedMapLayer[];
  /** Ratio thresholds between the color classes, ascending: the US figure,
   * the "elevated" and "high" need thresholds, and where severity reaches 100. */
  ratio_breaks: number[];
  source: string;
}

export interface AnalyzeResponse {
  counties: CountyReport[];
  truncated: boolean;
  message: string | null;
}

// --- frontend-only -----------------------------------------------------------

/** Properties written by backend/scripts/prep_data.py. */
export interface CountyProperties {
  GEOID: string;
  NAME: string;
  NAMELSAD: string;
  STUSPS: string;
  STATEFP: string;
  centroid_lon: number;
  centroid_lat: number;
  land_sq_km: number;
}

export type CountyFeature = GeoJSON.Feature<
  GeoJSON.Polygon | GeoJSON.MultiPolygon,
  CountyProperties
>;
export type CountyCollection = GeoJSON.FeatureCollection<
  GeoJSON.Polygon | GeoJSON.MultiPolygon,
  CountyProperties
>;

/** [west, south, east, north] */
export type Bounds = [number, number, number, number];

/** A Site Scout run as the browser follows it, scoped to one opportunity. */
export interface ScoutRun {
  fips: string;
  opportunityId: string;
  title: string;
  running: boolean;
  steps: ScoutStep[];
  candidates: SiteCandidate[];
  result: ScoutResult | null;
  error: string | null;
}
