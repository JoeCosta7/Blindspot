import type { Map as MapboxMap } from "mapbox-gl";
import {
  BBOX_MIN_AREA_SHARE,
  MAX_COUNTIES,
  boxPreviewLabel,
  countiesMatchingBox,
} from "../countyBox";
import type { CountyFeature, LonLat } from "../types";

const MIN_DRAG_PX = 6;

export interface BoxSelectOptions {
  onSelect: (sw: LonLat, ne: LonLat) => void;
  /** County polygons used for the live match count while dragging. */
  counties: CountyFeature[];
  maxCounties?: number;
  minAreaShare?: number;
}

/**
 * Lets the user drag a rectangle on the map. Panning is disabled while this is
 * active and restored by the returned cleanup function. Escape cancels a drag.
 *
 * `onSelect` receives the south-west and north-east corners as [lon, lat].
 * While dragging, the rectangle shows how many counties match (and the cap).
 */
export function startBoxSelect(map: MapboxMap, options: BoxSelectOptions): () => void {
  const { onSelect, counties, maxCounties = MAX_COUNTIES, minAreaShare = BBOX_MIN_AREA_SHARE } =
    options;
  const container = map.getCanvasContainer();
  let start: { x: number; y: number } | null = null;
  let box: HTMLDivElement | null = null;
  let label: HTMLSpanElement | null = null;
  let raf = 0;

  map.dragPan.disable();
  map.boxZoom.disable();
  container.classList.add("box-selecting");

  const position = (event: MouseEvent) => {
    const rect = container.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const clearDrag = () => {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    box?.remove();
    box = null;
    label = null;
    start = null;
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  };

  const updatePreview = (from: { x: number; y: number }, to: { x: number; y: number }) => {
    if (!box) {
      box = document.createElement("div");
      box.className = "box-select-rect";
      label = document.createElement("span");
      label.className = "box-select-count";
      box.appendChild(label);
      container.appendChild(box);
    }
    const left = Math.min(from.x, to.x);
    const top = Math.min(from.y, to.y);
    const width = Math.abs(to.x - from.x);
    const height = Math.abs(to.y - from.y);
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.style.width = `${width}px`;
    box.style.height = `${height}px`;

    if (width < MIN_DRAG_PX || height < MIN_DRAG_PX) {
      if (label) label.textContent = "Drag to select counties";
      return;
    }

    const a = map.unproject([from.x, from.y]);
    const b = map.unproject([to.x, to.y]);
    const sw: LonLat = [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat)];
    const ne: LonLat = [Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)];
    const matchCount = countiesMatchingBox(counties, sw, ne, minAreaShare).length;
    if (label) label.textContent = boxPreviewLabel(matchCount, maxCounties);
  };

  function onMouseMove(event: MouseEvent) {
    if (!start) return;
    const from = start;
    const to = position(event);
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      raf = 0;
      updatePreview(from, to);
    });
  }

  function onMouseUp(event: MouseEvent) {
    if (!start) return;
    const from = start;
    const to = position(event);
    clearDrag();
    if (Math.abs(to.x - from.x) < MIN_DRAG_PX || Math.abs(to.y - from.y) < MIN_DRAG_PX) return;

    const a = map.unproject([from.x, from.y]);
    const b = map.unproject([to.x, to.y]);
    onSelect(
      [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat)],
      [Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)],
    );
  }

  function onMouseDown(event: MouseEvent) {
    if (event.button !== 0) return;
    event.preventDefault();
    start = position(event);
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") clearDrag();
  }

  container.addEventListener("mousedown", onMouseDown, true);
  document.addEventListener("keydown", onKeyDown);

  return () => {
    clearDrag();
    container.removeEventListener("mousedown", onMouseDown, true);
    document.removeEventListener("keydown", onKeyDown);
    container.classList.remove("box-selecting");
    map.dragPan.enable();
    map.boxZoom.enable();
  };
}
