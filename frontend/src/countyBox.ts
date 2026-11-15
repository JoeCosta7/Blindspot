import { featureBounds } from "./format";
import type { CountyFeature, LonLat } from "./types";

/** Matches backend `Settings.max_counties` / `MAX_COUNTIES` (default 4). */
export const MAX_COUNTIES = 4;

/** Matches backend `Settings.bbox_min_area_share`. */
export const BBOX_MIN_AREA_SHARE = 0.25;

function rectOverlapArea(
  a: { west: number; south: number; east: number; north: number },
  b: { west: number; south: number; east: number; north: number },
): number {
  const west = Math.max(a.west, b.west);
  const south = Math.max(a.south, b.south);
  const east = Math.min(a.east, b.east);
  const north = Math.min(a.north, b.north);
  if (east <= west || north <= south) return 0;
  return (east - west) * (north - south);
}

/**
 * Counties that a drawn box would select, nearest to the box's center first.
 *
 * Mirrors backend `CountyIndex.resolve_bbox`: keep a county if its centroid is
 * inside the box or at least `minAreaShare` of its area overlaps. Area share
 * here uses each county's bounding box (fast preview; the server uses the
 * real polygon), so the live label may say "~".
 */
export function countiesMatchingBox(
  features: CountyFeature[],
  sw: LonLat,
  ne: LonLat,
  minAreaShare: number = BBOX_MIN_AREA_SHARE,
): { fips: string; distance: number }[] {
  const west = Math.min(sw[0], ne[0]);
  const south = Math.min(sw[1], ne[1]);
  const east = Math.max(sw[0], ne[0]);
  const north = Math.max(sw[1], ne[1]);
  const selection = { west, south, east, north };
  const centerLon = (west + east) / 2;
  const centerLat = (south + north) / 2;
  // Same scaling as the backend: a degree of longitude shrinks toward the poles.
  const lonScale = Math.cos((centerLat * Math.PI) / 180);
  const matches: { fips: string; distance: number }[] = [];

  for (const feature of features) {
    const [cWest, cSouth, cEast, cNorth] = featureBounds(feature);
    const countyBox = { west: cWest, south: cSouth, east: cEast, north: cNorth };
    const overlap = rectOverlapArea(selection, countyBox);
    if (overlap <= 0) continue;

    const { centroid_lon: lon, centroid_lat: lat, GEOID } = feature.properties;
    const centroidInside = west <= lon && lon <= east && south <= lat && lat <= north;
    const countyArea = Math.max((cEast - cWest) * (cNorth - cSouth), 1e-12);
    const share = overlap / countyArea;
    if (centroidInside || share >= minAreaShare) {
      const distance = Math.hypot((lon - centerLon) * lonScale, lat - centerLat);
      matches.push({ fips: GEOID, distance });
    }
  }

  matches.sort((a, b) => a.distance - b.distance || a.fips.localeCompare(b.fips));
  return matches;
}

/** Live label while the user drags a selection box. */
export function boxPreviewLabel(matchCount: number, maxCounties: number = MAX_COUNTIES): string {
  if (matchCount <= 0) return "No counties in this box yet";
  if (matchCount <= maxCounties) {
    return matchCount === 1
      ? "This box covers 1 county"
      : `This box covers ${matchCount} counties`;
  }
  return `This box covers ~${matchCount} counties (showing ${maxCounties})`;
}
