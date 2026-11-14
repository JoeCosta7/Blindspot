import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { useEffect, useRef, useState } from "react";
import { ASSET_STYLES, ASSET_TYPE_ORDER, formatAcres, formatNumber } from "../format";
import type { Asset, AssetType, Bounds, CountyCollection, LonLat } from "../types";
import { startBoxSelect } from "./boxSelect";
import { type LoadingArea, playFinishFlare, startLoadingSweep } from "./loadingSweep";

const TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;

const COUNTY_SOURCE = "counties";
const ASSET_SOURCE = "assets";
const COUNTY_FILL = "counties-fill";
const assetLayer = (type: AssetType) => `assets-${type}`;

export type SelectMode = "county" | "box";
export type Basemap = "streets" | "satellite";
export type Theme = "light" | "dark";

function styleUrl(basemap: Basemap, theme: Theme): string {
  if (basemap === "satellite") return "mapbox://styles/mapbox/satellite-streets-v12";
  return theme === "dark" ? "mapbox://styles/mapbox/dark-v11" : "mapbox://styles/mapbox/light-v11";
}

// Satellite photos in dark mode: brightest pixel capped at 55% and colors
// slightly muted, so the photos sit back behind labels and markers.
const SATELLITE_DARK_BRIGHTNESS = 0.55;
const SATELLITE_DARK_SATURATION = -0.2;

/** County outline and highlight colors that read on each basemap. */
function countyColors(basemap: Basemap, theme: Theme) {
  if (basemap === "satellite") {
    return { accent: "#4ade80", line: "rgba(255, 255, 255, 0.55)", lineWidth: 0.6 };
  }
  if (theme === "dark") return { accent: "#4ade80", line: "#8b98a3", lineWidth: 0.6 };
  return { accent: "#15803d", line: "#94a3b8", lineWidth: 0.4 };
}

/** Sweep and flare colors that read on each basemap. */
function sweepColors(basemap: Basemap, theme: Theme) {
  if (basemap === "satellite" || theme === "dark") return { line: "#bbf7d0", glow: "#4ade80" };
  return { line: "#15803d", glow: "#22c55e" };
}

interface Props {
  counties: CountyCollection | null;
  mode: SelectMode;
  basemap: Basemap;
  theme: Theme;
  onBasemapChange: (basemap: Basemap) => void;
  selectedFips: string[];
  /** Fit the map to this box when it changes. */
  fitTo: Bounds | null;
  assets: Asset[];
  /** Fly to this asset and open its popup. */
  focusAsset: Asset | null;
  /** While a report loads, the area to cover with the loading animation. */
  loadingArea: LoadingArea | null;
  /** Played once each time a new object arrives: the report has loaded. */
  flare: { area: LoadingArea } | null;
  onCountyClick: (fips: string) => void;
  onBoxSelect: (sw: LonLat, ne: LonLat) => void;
}

function popupContent(asset: Asset): HTMLElement {
  // Built with textContent: names come from OpenStreetMap and EPA files.
  const root = document.createElement("div");
  root.className = "asset-popup";
  const name = document.createElement("strong");
  name.textContent = asset.name;
  const type = document.createElement("div");
  type.textContent = ASSET_STYLES[asset.type].label;
  root.append(name, type);

  const facts: string[] = [];
  const {
    acres,
    city,
    project_status,
    level2_ports,
    dc_fast_ports,
    transmission_miles,
    waterway,
    capacity_mw,
    owner_type,
  } = asset.attributes;
  if (typeof city === "string") facts.push(city);
  if (typeof acres === "number") facts.push(formatAcres(acres));
  if (typeof transmission_miles === "number") {
    facts.push(`${formatNumber(transmission_miles)} miles to a transmission line (EPA)`);
  }
  if (typeof waterway === "string") facts.push(`On ${waterway}`);
  if (typeof owner_type === "string") facts.push(`Owner: ${owner_type}`);
  if (typeof capacity_mw === "number") {
    facts.push(`${formatNumber(capacity_mw)} MW hydropower potential (ORNL model estimate)`);
  }
  if (typeof project_status === "string") facts.push(`Gas project status: ${project_status}`);
  if (typeof level2_ports === "number") facts.push(`${level2_ports} Level 2 ports`);
  if (typeof dc_fast_ports === "number") facts.push(`${dc_fast_ports} fast-charge ports`);
  for (const fact of facts) {
    const line = document.createElement("div");
    line.textContent = fact;
    root.appendChild(line);
  }
  const source = document.createElement("div");
  source.className = "asset-popup-source";
  source.textContent = `Source: ${asset.source}`;
  root.appendChild(source);
  return root;
}

function toFeatureCollection(assets: Asset[]): GeoJSON.FeatureCollection<GeoJSON.Point> {
  return {
    type: "FeatureCollection",
    features: assets.map((asset) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [asset.lon, asset.lat] },
      properties: { id: asset.id, type: asset.type },
    })),
  };
}

export function MapView(props: Props) {
  const {
    counties,
    mode,
    basemap,
    theme,
    onBasemapChange,
    selectedFips,
    fitTo,
    assets,
    focusAsset,
    loadingArea,
    flare,
    onCountyClick,
    onBoxSelect,
  } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const popupRef = useRef<mapboxgl.Popup | null>(null);
  const styleRef = useRef<string | null>(null);
  const handlersBound = useRef(false);
  // Bumped each time a style finishes loading. Changing the style wipes our
  // sources and layers, so they are added again on every bump.
  const [styleVersion, setStyleVersion] = useState(0);
  // Bumped each time our layers are (re)added.
  const [layersVersion, setLayersVersion] = useState(0);
  const layersReady = layersVersion > 0;
  const [mapError, setMapError] = useState<string | null>(null);
  // Set once Mapbox refuses satellite imagery (usage limit or no access).
  // Satellite then stays off for this page visit so no more photo tiles are
  // requested; reloading the page tries again.
  const [satelliteBlocked, setSatelliteBlocked] = useState(false);

  // Event handlers registered once read the latest props through refs.
  const latest = useRef({ mode, assets, basemap, theme, onCountyClick, onBoxSelect, onBasemapChange });
  latest.current = { mode, assets, basemap, theme, onCountyClick, onBoxSelect, onBasemapChange };
  const highlighted = useRef<string[]>([]);

  // --- create the map -------------------------------------------------------
  useEffect(() => {
    if (!TOKEN || !containerRef.current) return;
    const style = styleUrl(latest.current.basemap, latest.current.theme);
    styleRef.current = style;
    const map = new mapboxgl.Map({
      accessToken: TOKEN,
      container: containerRef.current,
      style,
      center: [-96.5, 38.5],
      zoom: 3.6,
      minZoom: 2.5,
    });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");
    map.on("style.load", () => setStyleVersion((v) => v + 1));

    let stopped = false;
    // Removing the map cancels its pending requests and makes no new ones.
    const stopMap = (message: string) => {
      if (stopped) return;
      stopped = true;
      popupRef.current?.remove();
      map.remove();
      mapRef.current = null;
      setMapError(message);
    };

    map.on("error", (event) => {
      const error = event.error as { status?: number; url?: string } | undefined;
      const status = error?.status;
      const isSatellite = (error?.url ?? "").includes("mapbox.satellite");

      if (status === 429 && isSatellite) {
        // Only the photos are refused: go back to the plain map, which uses a
        // separate allowance, and stop asking for photos.
        setSatelliteBlocked(true);
        if (latest.current.basemap === "satellite") latest.current.onBasemapChange("streets");
        return;
      }
      if (status === 429) {
        stopMap(
          "Mapbox usage limit reached, so the map was stopped to avoid more requests. " +
            "Check your usage at account.mapbox.com, then reload the page.",
        );
        return;
      }
      if (status === 401 || status === 403) {
        stopMap("Mapbox rejected the token. Check VITE_MAPBOX_TOKEN in frontend/.env.");
      }
    });
    mapRef.current = map;
    return () => {
      if (stopped) return;
      stopped = true;
      popupRef.current?.remove();
      map.remove();
      mapRef.current = null;
      handlersBound.current = false;
      setStyleVersion(0);
      setLayersVersion(0);
    };
  }, []);

  // --- basemap and theme ----------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    const style = styleUrl(basemap, theme);
    if (!map || styleRef.current === style) return;
    styleRef.current = style;
    // A clean swap: "style.load" then fires and the layers below are re-added.
    // The cast: Mapbox's types mark its font options required, but the map
    // fills them in itself, and passing undefined would override its defaults.
    map.setStyle(style, { diff: false } as Parameters<typeof map.setStyle>[1]);
  }, [basemap, theme]);

  // --- dim satellite photos in dark mode ----------------------------------------
  // Only the photo (raster) layers: labels, roads and our layers stay bright.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || styleVersion === 0 || basemap !== "satellite") return;
    const dark = theme === "dark";
    for (const layer of map.getStyle()?.layers ?? []) {
      if (layer.type !== "raster") continue;
      map.setPaintProperty(layer.id, "raster-brightness-max", dark ? SATELLITE_DARK_BRIGHTNESS : 1);
      map.setPaintProperty(layer.id, "raster-saturation", dark ? SATELLITE_DARK_SATURATION : 0);
    }
  }, [styleVersion, basemap, theme]);

  // --- county and asset layers ------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || styleVersion === 0 || !counties || map.getSource(COUNTY_SOURCE)) return;
    const colors = countyColors(latest.current.basemap, latest.current.theme);

    map.addSource(COUNTY_SOURCE, { type: "geojson", data: counties, promoteId: "GEOID" });
    map.addLayer({
      id: COUNTY_FILL,
      type: "fill",
      source: COUNTY_SOURCE,
      paint: {
        "fill-color": colors.accent,
        "fill-opacity": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          0.14,
          ["boolean", ["feature-state", "hover"], false],
          0.1,
          0, // transparent, but still hit-tested
        ],
      },
    });
    map.addLayer({
      id: "counties-line",
      type: "line",
      source: COUNTY_SOURCE,
      paint: {
        "line-color": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          colors.accent,
          colors.line,
        ],
        "line-width": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          2,
          colors.lineWidth,
        ],
      },
    });

    map.addSource(ASSET_SOURCE, { type: "geojson", data: toFeatureCollection([]) });
    for (const type of ASSET_TYPE_ORDER) {
      map.addLayer({
        id: assetLayer(type),
        type: "circle",
        source: ASSET_SOURCE,
        filter: ["==", ["get", "type"], type],
        paint: {
          "circle-color": ASSET_STYLES[type].color,
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 3, 10, 6, 14, 9],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.2,
        },
      });
    }
    // Feature state and data did not survive the style swap; the effects
    // keyed on layersVersion restore them.
    highlighted.current = [];
    setLayersVersion((v) => v + 1);

    // Layer-bound handlers are kept by the map across style swaps, so they are
    // registered once.
    if (handlersBound.current) return;
    handlersBound.current = true;
    const assetLayers = ASSET_TYPE_ORDER.map(assetLayer);

    let hovered: string | null = null;
    const setHover = (id: string | null) => {
      if (hovered === id) return;
      if (hovered) map.setFeatureState({ source: COUNTY_SOURCE, id: hovered }, { hover: false });
      if (id) map.setFeatureState({ source: COUNTY_SOURCE, id }, { hover: true });
      hovered = id;
    };
    map.on("mousemove", COUNTY_FILL, (event) => {
      const id = event.features?.[0]?.id;
      setHover(latest.current.mode === "county" && typeof id === "string" ? id : null);
      map.getCanvas().style.cursor = latest.current.mode === "county" ? "pointer" : "";
    });
    map.on("mouseleave", COUNTY_FILL, () => {
      setHover(null);
      map.getCanvas().style.cursor = "";
    });

    map.on("click", (event) => {
      if (latest.current.mode !== "county") return;
      const [marker] = map.queryRenderedFeatures(event.point, { layers: assetLayers });
      if (marker) {
        const asset = latest.current.assets.find((a) => a.id === marker.properties?.id);
        if (asset) {
          popupRef.current?.remove();
          popupRef.current = new mapboxgl.Popup({ offset: 10, maxWidth: "280px" })
            .setLngLat([asset.lon, asset.lat])
            .setDOMContent(popupContent(asset))
            .addTo(map);
        }
        return; // a marker click is not a county click
      }
      const [county] = map.queryRenderedFeatures(event.point, { layers: [COUNTY_FILL] });
      if (typeof county?.id === "string") latest.current.onCountyClick(county.id);
    });
  }, [styleVersion, counties]);

  // --- selection highlight ----------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || layersVersion === 0) return;
    for (const id of highlighted.current) {
      map.setFeatureState({ source: COUNTY_SOURCE, id }, { selected: false });
    }
    for (const id of selectedFips) {
      map.setFeatureState({ source: COUNTY_SOURCE, id }, { selected: true });
    }
    highlighted.current = selectedFips;
  }, [layersVersion, selectedFips]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !layersReady || !fitTo) return;
    const [west, south, east, north] = fitTo;
    map.fitBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding: 60, maxZoom: 10, duration: 900 },
    );
  }, [layersReady, fitTo]);

  // --- markers ------------------------------------------------------------------
  // New results close an open popup; a basemap swap does not.
  useEffect(() => {
    popupRef.current?.remove();
  }, [assets]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || layersVersion === 0) return;
    (map.getSource(ASSET_SOURCE) as mapboxgl.GeoJSONSource).setData(toFeatureCollection(assets));
  }, [layersVersion, assets]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !layersReady || !focusAsset) return;
    map.flyTo({
      center: [focusAsset.lon, focusAsset.lat],
      zoom: Math.max(map.getZoom(), 12),
      duration: 900,
    });
    popupRef.current?.remove();
    popupRef.current = new mapboxgl.Popup({ offset: 10, maxWidth: "280px" })
      .setLngLat([focusAsset.lon, focusAsset.lat])
      .setDOMContent(popupContent(focusAsset))
      .addTo(map);
  }, [layersReady, focusAsset]);

  // --- loading animation ---------------------------------------------------------
  // Keyed on layersVersion so a basemap swap mid-load puts it back.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || layersVersion === 0 || !loadingArea) return;
    const colors = sweepColors(latest.current.basemap, latest.current.theme);
    return startLoadingSweep(map, loadingArea, colors);
  }, [layersVersion, loadingArea]);

  // Not keyed on layersVersion: a basemap swap should not replay it.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !layersReady || !flare) return;
    const colors = sweepColors(latest.current.basemap, latest.current.theme);
    return playFinishFlare(map, flare.area, colors);
  }, [layersReady, flare]);

  // --- box select -----------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !layersReady || mode !== "box") return;
    return startBoxSelect(map, (sw, ne) => latest.current.onBoxSelect(sw, ne));
  }, [layersReady, mode]);

  const present = ASSET_TYPE_ORDER.filter((type) => assets.some((a) => a.type === type));

  if (!TOKEN || mapError) {
    return (
      <div className="map map-placeholder">
        <div>
          <h2>Map unavailable</h2>
          <p>
            {mapError ??
              "Set VITE_MAPBOX_TOKEN in frontend/.env and restart the dev server to see the map."}
          </p>
          <p>You can still search for a county by name. Reports do not need the map.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="map">
      <div ref={containerRef} className="map-canvas" />
      <div className="basemap-toggle" role="group" aria-label="Map view">
        <button
          type="button"
          className={basemap === "streets" ? "active" : undefined}
          aria-pressed={basemap === "streets"}
          onClick={() => onBasemapChange("streets")}
        >
          Map
        </button>
        <button
          type="button"
          className={basemap === "satellite" ? "active" : undefined}
          aria-pressed={basemap === "satellite"}
          disabled={satelliteBlocked}
          title={satelliteBlocked ? "Mapbox refused satellite imagery for this token" : undefined}
          onClick={() => onBasemapChange("satellite")}
        >
          Satellite
        </button>
      </div>
      {satelliteBlocked && (
        <div className="map-notice" role="status">
          Satellite view is off: Mapbox refused the imagery (usage limit or no access). Check
          account.mapbox.com, then reload.
        </div>
      )}
      {mode === "box" && (
        <div className="map-hint">Drag a box over the counties to analyze. Esc cancels.</div>
      )}
      {present.length > 0 && (
        <div className="legend">
          {present.map((type) => (
            <div key={type} className="legend-row">
              <span className="legend-dot" style={{ background: ASSET_STYLES[type].color }} />
              {ASSET_STYLES[type].plural}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
