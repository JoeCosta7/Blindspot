import type {
  AssetType,
  Bounds,
  CountyFeature,
  FundingStatus,
  NeedTheme,
  NeedUnit,
} from "./types";

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });
const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const dollars = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export const formatNumber = (value: number): string => number.format(value);
export const formatWhole = (value: number): string => whole.format(value);
export const formatDollars = (value: number): string => dollars.format(value);
export const formatShare = (value: number): string => `${(value * 100).toFixed(1)}%`;

/** Small sites keep a decimal: "0.3 acres", not "0 acres". */
export function formatAcres(acres: number): string {
  const digits = acres < 10 ? 1 : 0;
  const text = acres.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
  if (text === "0") return "under 0.1 acres";
  return `${text} ${text === "1" ? "acre" : "acres"}`;
}

export function formatNeedValue(value: number | null, unit: NeedUnit): string | null {
  if (value === null) return null;
  switch (unit) {
    case "share":
      return formatShare(value);
    case "usd":
      return formatDollars(value);
    case "per_10k_residents":
      return `${value.toFixed(1)} per 10,000 residents`;
    case "count":
      return formatWhole(value);
    case "usd_per_month":
      return `${formatDollars(value)} a month`;
    case "lb_co2_per_mwh":
      return `${formatWhole(value)} lb CO2 per MWh`;
    case "rating":
      return null;
  }
}

/** "2027-01-21" -> "Jan 21, 2027", without a timezone shift. */
export function formatDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  if (!year || !month || !day) return iso;
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export const THEME_LABELS: Record<NeedTheme, string> = {
  equity: "Equity",
  energy: "Energy",
  transportation: "Transportation",
  waste: "Waste",
  resilience: "Resilience",
};

interface AssetStyle {
  label: string;
  plural: string;
  color: string;
}

/** One map layer and legend entry per asset type. */
export const ASSET_STYLES: Record<AssetType, AssetStyle> = {
  brownfield: { label: "Brownfield", plural: "Brownfields", color: "#b45309" },
  contaminated_site: {
    label: "Cleanup site",
    plural: "Cleanup sites (Superfund, RCRA, state)",
    color: "#78716c",
  },
  landfill: { label: "Landfill", plural: "Landfills (EPA RE-Powering)", color: "#7c3aed" },
  mine_land: { label: "Mine land", plural: "Abandoned mine lands", color: "#1f2937" },
  lmop_landfill: {
    label: "Landfill (gas database)",
    plural: "Landfills (EPA gas database)",
    color: "#db2777",
  },
  school: { label: "School", plural: "Schools", color: "#2563eb" },
  library: { label: "Library", plural: "Libraries", color: "#0891b2" },
  college: { label: "College", plural: "Colleges", color: "#4f46e5" },
  ev_station: { label: "EV charging station", plural: "EV charging stations", color: "#16a34a" },
  non_powered_dam: {
    label: "Dam without hydropower",
    plural: "Dams without hydropower (ORNL)",
    color: "#0f766e",
  },
};

export const ASSET_TYPE_ORDER = Object.keys(ASSET_STYLES) as AssetType[];

interface StatusStyle {
  label: string;
  /** Whether someone can act on the program today. Never true for
   * `uncertain` or `unverified`. */
  available: boolean;
}

export const FUNDING_STATUS: Record<FundingStatus, StatusStyle> = {
  open: { label: "Open", available: true },
  open_with_deadlines: { label: "Open, with deadlines", available: true },
  paused: { label: "Paused", available: false },
  uncertain: { label: "Status uncertain", available: false },
  unverified: { label: "Not verified", available: false },
};

/** Names for `source_status.source`. Kept in step with pipeline.SOURCE_NAMES. */
export const SOURCE_NAMES: Record<string, string> = {
  census: "Census ACS (county)",
  census_national: "Census ACS (national baseline)",
  nri: "FEMA National Risk Index",
  repowering: "EPA RE-Powering sites",
  lmop: "EPA landfill database",
  geoapify: "OpenStreetMap public buildings",
  overpass: "OpenStreetMap public buildings", // reports saved before Geoapify
  ev_stations: "EV charging stations",
  ev_national_count: "National EV station count",
  pvwatts: "PVWatts solar yield",
  state_energy: "State electricity bills and grid carbon (EIA, EPA eGRID)",
  dams: "ORNL non-powered dams",
  farms: "USDA farm census and EPA digester database",
  gemini: "Gemini narrative",
  gemini_solutions: "Gemini suggested solutions",
};

export const sourceName = (source: string): string => SOURCE_NAMES[source] ?? source;

export function featureBounds(feature: CountyFeature): Bounds {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  const polygons =
    feature.geometry.type === "Polygon"
      ? [feature.geometry.coordinates]
      : feature.geometry.coordinates;
  for (const polygon of polygons) {
    for (const [lon, lat] of polygon[0] ?? []) {
      if (lon === undefined || lat === undefined) continue;
      if (lon < west) west = lon;
      if (lon > east) east = lon;
      if (lat < south) south = lat;
      if (lat > north) north = lat;
    }
  }
  return [west, south, east, north];
}

export function unionBounds(all: Bounds[]): Bounds | null {
  if (all.length === 0) return null;
  return [
    Math.min(...all.map((b) => b[0])),
    Math.min(...all.map((b) => b[1])),
    Math.max(...all.map((b) => b[2])),
    Math.max(...all.map((b) => b[3])),
  ];
}
