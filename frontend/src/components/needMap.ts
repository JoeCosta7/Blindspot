import type { NeedMapLayer } from "../types";
import type { Basemap, Theme } from "./MapView";

// One hue, five steps (dataviz skill sequential blue, steps 250-700). Checked
// with its validate_palette.js --ordinal on 2026-11-14 against light and dark
// surfaces. On the dark basemap the ramp runs the other way, so the worst
// class is always the one that stands furthest from the map behind it.
const RAMP_LIGHT = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"];
const RAMP_DARK = ["#184f95", "#256abf", "#3987e5", "#86b6ef", "#cde2fb"];

export const NEED_CLASS_STATE = "needClass";
export const NEED_FILL_OPACITY = 0.78;

/** Class colors, best class first. */
export function needRamp(basemap: Basemap, theme: Theme): string[] {
  return basemap === "streets" && theme === "dark" ? RAMP_DARK : RAMP_LIGHT;
}

/** 0 (better than the US figure) up to `breaks.length` (furthest from it). */
export function needClass(ratio: number, breaks: number[]): number {
  return breaks.filter((edge) => ratio >= edge).length;
}

const percent = (edge: number): string => `${Math.round((edge - 1) * 100)}%`;

/** One legend label per class, from the same thresholds the needs use. */
export function needClassLabels(layer: NeedMapLayer, breaks: number[]): string[] {
  const [, elevated, high, top] = breaks;
  if (elevated === undefined || high === undefined || top === undefined) return [];
  // Income is the one need where the lower figure is the worse one. Its ratio
  // is the US figure over the county's, so the labels say it that way round.
  if (layer.lower_is_worse) {
    return [
      "County is above the US figure",
      `US figure up to ${percent(elevated)} higher`,
      `US figure ${percent(elevated)} to ${percent(high)} higher · elevated need`,
      `US figure ${percent(high)} to ${percent(top)} higher · high need`,
      `US figure ${percent(top)} or more higher · high need`,
    ];
  }
  return [
    "Below the US figure",
    `Up to ${percent(elevated)} above it`,
    `${percent(elevated)} to ${percent(high)} above · elevated need`,
    `${percent(high)} to ${percent(top)} above · high need`,
    `${percent(top)} or more above · high need`,
  ];
}
