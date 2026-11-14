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
  | "low_tree_canopy";

export type NeedTheme = "equity" | "energy" | "transportation" | "waste" | "resilience";
export type NeedLevel = "elevated" | "high";
export type NeedUnit =
  | "share"
  | "usd"
  | "per_10k_residents"
  | "count"
  | "rating"
  | "usd_per_month"
  | "lb_co2_per_mwh"
  | "celsius";

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
  | "non_powered_dam";

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
}

export interface Opportunity {
  id: string;
  solution_id: string;
  title: string;
  /** Null for a county-wide opportunity with no single site. */
  asset_id: string | null;
  need_ids: NeedId[];
  score: number;
  estimates: Estimate[];
  funding: FundingMatch[];
}

export interface OpportunityNote {
  opportunity_id: string;
  text: string;
}

export interface Narrative {
  summary: string;
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
  opportunities: Opportunity[];
  narrative: Narrative;
  source_status: SourceStatus[];
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
