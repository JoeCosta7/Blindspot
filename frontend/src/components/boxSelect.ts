import type { Map as MapboxMap } from "mapbox-gl";
import type { LonLat } from "../types";

const MIN_DRAG_PX = 6;

/**
 * Lets the user drag a rectangle on the map. Panning is disabled while this is
 * active and restored by the returned cleanup function. Escape cancels a drag.
 *
 * `onSelect` receives the south-west and north-east corners as [lon, lat].
 */
export function startBoxSelect(
  map: MapboxMap,
  onSelect: (sw: LonLat, ne: LonLat) => void,
): () => void {
  const container = map.getCanvasContainer();
  let start: { x: number; y: number } | null = null;
  let box: HTMLDivElement | null = null;

  map.dragPan.disable();
  map.boxZoom.disable();
  container.classList.add("box-selecting");

  const position = (event: MouseEvent) => {
    const rect = container.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const clearDrag = () => {
    box?.remove();
    box = null;
    start = null;
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  };

  function onMouseMove(event: MouseEvent) {
    if (!start) return;
    const current = position(event);
    if (!box) {
      box = document.createElement("div");
      box.className = "box-select-rect";
      container.appendChild(box);
    }
    box.style.left = `${Math.min(start.x, current.x)}px`;
    box.style.top = `${Math.min(start.y, current.y)}px`;
    box.style.width = `${Math.abs(current.x - start.x)}px`;
    box.style.height = `${Math.abs(current.y - start.y)}px`;
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
