import type { GeoJSONSource, Map as MapboxMap } from "mapbox-gl";

/**
 * Map animations for the area being analyzed.
 *
 * - startLoadingSweep: while a report loads, a diagonal line sweeps across the
 *   area from south-west to north-east, again and again, clipped exactly to
 *   its boundary, with fading ripples trailing behind it.
 * - playFinishFlare: when the report arrives, the area flashes and its outline
 *   pulses outward once.
 */

export type LoadingArea = GeoJSON.Polygon | GeoJSON.MultiPolygon;

export interface SweepColors {
  line: string;
  glow: string;
}

const SWEEP_SOURCE = "loading-sweep";
const SWEEP_GLOW = "loading-sweep-glow";
const SWEEP_CORE = "loading-sweep-core";

/** Seconds for one pass across the area. */
const PASS_S = 1.8;
/** Trailing ripples behind the main line, as a share of the area's width. */
const RIPPLE_GAP = 0.07;
const RIPPLES = 3;
const FRAME_MS = 16;

const FLARE_SOURCE = "loading-flare";
const FLARE_FILL = "loading-flare-fill";
const FLARE_LINE = "loading-flare-line";
const FLARE_MS = 1100;

type Point = [number, number];

/** Rings in local units: x = lon * kx, y = lat. Close to Mercator's aspect at county scale. */
function localRings(area: LoadingArea) {
  const polygons = area.type === "Polygon" ? [area.coordinates] : area.coordinates;
  let latSum = 0;
  let count = 0;
  for (const rings of polygons) {
    for (const p of rings[0] ?? []) {
      latSum += p[1] ?? 0;
      count++;
    }
  }
  const kx = Math.cos(((latSum / (count || 1)) * Math.PI) / 180);
  const rings: Point[][] = [];
  for (const polygon of polygons) {
    for (const ring of polygon) {
      rings.push(ring.map(([lon = 0, lat = 0]) => [lon * kx, lat]));
    }
  }
  return { rings, kx };
}

// The sweep line is perpendicular to the south-west -> north-east direction.
const N: Point = [Math.SQRT1_2, Math.SQRT1_2];
const dotN = (p: Point) => p[0] * N[0] + p[1] * N[1];
const along = (p: Point) => -p[0] * N[1] + p[1] * N[0];

/**
 * The line {p : p·N = d} clipped to the rings, as segments. Pairing sorted
 * crossings is the even-odd rule, so holes and multi-part areas just work.
 */
function clipLine(rings: Point[][], d: number): Point[][] {
  const hits: Point[] = [];
  for (const ring of rings) {
    for (let i = 0; i < ring.length - 1; i++) {
      const p = ring[i] as Point;
      const q = ring[i + 1] as Point;
      const a = dotN(p) - d;
      const b = dotN(q) - d;
      if (a > 0 === b > 0) continue;
      const t = a / (a - b);
      hits.push([p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]);
    }
  }
  hits.sort((u, v) => along(u) - along(v));
  const segments: Point[][] = [];
  for (let i = 0; i + 1 < hits.length; i += 2) segments.push([hits[i] as Point, hits[i + 1] as Point]);
  return segments;
}

function sweepFrame(
  rings: Point[][],
  kx: number,
  dMin: number,
  dMax: number,
  seconds: number,
): GeoJSON.FeatureCollection<GeoJSON.MultiLineString> {
  const span = dMax - dMin;
  const gap = span * RIPPLE_GAP;
  // Start before the area and end after it so the ripples clear the far edge.
  const travel = span + gap * RIPPLES * 2;
  const progress = (seconds / PASS_S) % 1;
  const eased = progress < 0.5 ? 2 * progress * progress : 1 - (-2 * progress + 2) ** 2 / 2;
  const head = dMin - gap + travel * eased;

  const features: GeoJSON.Feature<GeoJSON.MultiLineString>[] = [];
  for (let k = 0; k <= RIPPLES; k++) {
    const segments = clipLine(rings, head - k * gap);
    if (segments.length === 0) continue;
    features.push({
      type: "Feature",
      geometry: {
        type: "MultiLineString",
        coordinates: segments.map((s) => s.map(([x, y]) => [x / kx, y])),
      },
      properties: { strength: 1 - k / (RIPPLES + 1) },
    });
  }
  return { type: "FeatureCollection", features };
}

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function removeAll(map: MapboxMap, layers: string[], source: string) {
  // The style may already have been swapped or the map removed.
  try {
    for (const layer of layers) if (map.getLayer(layer)) map.removeLayer(layer);
    if (map.getSource(source)) map.removeSource(source);
  } catch {
    // nothing left to remove
  }
}

/** Starts the sweep over `area`. Returns a function that removes it. */
export function startLoadingSweep(
  map: MapboxMap,
  area: LoadingArea,
  colors: SweepColors,
): () => void {
  const { rings, kx } = localRings(area);
  let dMin = Infinity;
  let dMax = -Infinity;
  for (const ring of rings) {
    for (const p of ring) {
      dMin = Math.min(dMin, dotN(p));
      dMax = Math.max(dMax, dotN(p));
    }
  }
  const animate = !reducedMotion();
  const start = performance.now();
  // With reduced motion, a still line through the middle shows that work is under way.
  const still = PASS_S / 2;

  map.addSource(SWEEP_SOURCE, {
    type: "geojson",
    data: sweepFrame(rings, kx, dMin, dMax, animate ? 0 : still),
  });
  map.addLayer({
    id: SWEEP_GLOW,
    type: "line",
    source: SWEEP_SOURCE,
    layout: { "line-cap": "round" },
    paint: {
      "line-color": colors.glow,
      "line-width": ["*", 14, ["get", "strength"]],
      "line-blur": 8,
      "line-opacity": ["*", 0.6, ["get", "strength"]],
    },
  });
  map.addLayer({
    id: SWEEP_CORE,
    type: "line",
    source: SWEEP_SOURCE,
    layout: { "line-cap": "round" },
    paint: {
      "line-color": colors.line,
      "line-width": ["*", 3, ["get", "strength"]],
      "line-opacity": ["get", "strength"],
    },
  });

  let raf = 0;
  let last = 0;
  const tick = (now: number) => {
    raf = requestAnimationFrame(tick);
    if (now - last < FRAME_MS) return;
    last = now;
    const source = map.getSource(SWEEP_SOURCE) as GeoJSONSource | undefined;
    source?.setData(sweepFrame(rings, kx, dMin, dMax, (now - start) / 1000));
  };
  if (animate) raf = requestAnimationFrame(tick);

  return () => {
    cancelAnimationFrame(raf);
    removeAll(map, [SWEEP_GLOW, SWEEP_CORE], SWEEP_SOURCE);
  };
}

/** One flash of the area and a pulse of its outline. Returns a function that stops it early. */
export function playFinishFlare(
  map: MapboxMap,
  area: LoadingArea,
  colors: SweepColors,
): () => void {
  if (reducedMotion()) return () => {};
  const data: GeoJSON.Feature = { type: "Feature", geometry: area, properties: {} };
  map.addSource(FLARE_SOURCE, { type: "geojson", data });
  map.addLayer({
    id: FLARE_FILL,
    type: "fill",
    source: FLARE_SOURCE,
    paint: { "fill-color": colors.glow, "fill-opacity": 0, "fill-opacity-transition": { duration: 0 } },
  });
  map.addLayer({
    id: FLARE_LINE,
    type: "line",
    source: FLARE_SOURCE,
    layout: { "line-join": "round" },
    paint: {
      "line-color": colors.line,
      "line-width": 2,
      "line-blur": 0,
      "line-opacity": 0,
      "line-width-transition": { duration: 0 },
      "line-blur-transition": { duration: 0 },
      "line-opacity-transition": { duration: 0 },
    },
  });

  const start = performance.now();
  let raf = 0;
  const tick = (now: number) => {
    const t = Math.min((now - start) / FLARE_MS, 1);
    const out = 1 - (1 - t) ** 3; // ease-out
    try {
      // Fill: a quick flash that fades. Outline: grows and blurs into a halo.
      map.setPaintProperty(FLARE_FILL, "fill-opacity", 0.45 * (1 - out) * Math.min(t * 8, 1));
      map.setPaintProperty(FLARE_LINE, "line-width", 2 + 22 * out);
      map.setPaintProperty(FLARE_LINE, "line-blur", 14 * out);
      map.setPaintProperty(FLARE_LINE, "line-opacity", 0.95 * (1 - t));
    } catch {
      return; // the style was swapped mid-flare; nothing to animate
    }
    if (t < 1) raf = requestAnimationFrame(tick);
    else removeAll(map, [FLARE_FILL, FLARE_LINE], FLARE_SOURCE);
  };
  raf = requestAnimationFrame(tick);

  return () => {
    cancelAnimationFrame(raf);
    removeAll(map, [FLARE_FILL, FLARE_LINE], FLARE_SOURCE);
  };
}
