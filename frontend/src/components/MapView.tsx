import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { useEffect, useRef, useState } from "react";
import { ASSET_STYLES, ASSET_TYPE_ORDER, formatAcres } from "../format";
import type { Asset, AssetType, Bounds, CountyCollection, LonLat } from "../types";
import { startBoxSelect } from "./boxSelect";

const TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;

const COUNTY_SOURCE = "counties";
const ASSET_SOURCE = "assets";
const COUNTY_FILL = "counties-fill";
const assetLayer = (type: AssetType) => `assets-${type}`;

export type SelectMode = "county" | "box";

interface Props {
  counties: CountyCollection | null;
  mode: SelectMode;
  selectedFips: string[];
  /** Fit the map to this box when it changes. */
  fitTo: Bounds | null;
  assets: Asset[];
  /** Fly to this asset and open its popup. */
  focusAsset: Asset | null;
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
  const { acres, city, project_status, level2_ports, dc_fast_ports } = asset.attributes;
  if (typeof city === "string") facts.push(city);
  if (typeof acres === "number") facts.push(formatAcres(acres));
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
  const { counties, mode, selectedFips, fitTo, assets, focusAsset, onCountyClick, onBoxSelect } =
    props;
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const popupRef = useRef<mapboxgl.Popup | null>(null);
  const [ready, setReady] = useState(false);
  const [layersReady, setLayersReady] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);

  // Event handlers registered once read the latest props through refs.
  const latest = useRef({ mode, assets, onCountyClick, onBoxSelect });
  latest.current = { mode, assets, onCountyClick, onBoxSelect };
  const highlighted = useRef<string[]>([]);

  // --- create the map -------------------------------------------------------
  useEffect(() => {
    if (!TOKEN || !containerRef.current) return;
    const map = new mapboxgl.Map({
      accessToken: TOKEN,
      container: containerRef.current,
      style: "mapbox://styles/mapbox/light-v11",
      center: [-96.5, 38.5],
      zoom: 3.6,
      minZoom: 2.5,
    });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");
    map.on("load", () => setReady(true));
    map.on("error", (event) => {
      const status = (event.error as { status?: number } | undefined)?.status;
      if (status === 401 || status === 403) {
        setMapError("Mapbox rejected the token. Check VITE_MAPBOX_TOKEN in frontend/.env.");
      }
    });
    mapRef.current = map;
    return () => {
      popupRef.current?.remove();
      map.remove();
      mapRef.current = null;
      setReady(false);
      setLayersReady(false);
    };
  }, []);

  // --- county and asset layers ------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !counties || map.getSource(COUNTY_SOURCE)) return;

    map.addSource(COUNTY_SOURCE, { type: "geojson", data: counties, promoteId: "GEOID" });
    map.addLayer({
      id: COUNTY_FILL,
      type: "fill",
      source: COUNTY_SOURCE,
      paint: {
        "fill-color": "#15803d",
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
          "#15803d",
          "#94a3b8",
        ],
        "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 2, 0.4],
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

    setLayersReady(true);
  }, [ready, counties]);

  // --- selection highlight ----------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !layersReady) return;
    for (const id of highlighted.current) {
      map.setFeatureState({ source: COUNTY_SOURCE, id }, { selected: false });
    }
    for (const id of selectedFips) {
      map.setFeatureState({ source: COUNTY_SOURCE, id }, { selected: true });
    }
    highlighted.current = selectedFips;
  }, [layersReady, selectedFips]);

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
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !layersReady) return;
    popupRef.current?.remove();
    (map.getSource(ASSET_SOURCE) as mapboxgl.GeoJSONSource).setData(toFeatureCollection(assets));
  }, [layersReady, assets]);

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
