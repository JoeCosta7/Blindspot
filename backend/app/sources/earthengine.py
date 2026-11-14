"""Google Earth Engine: urban heat and tree canopy on built-up land, per county.

One live call per county, cached for `ee_cache_ttl_days`. The Earth Engine
client is synchronous, so the work runs in a thread; the pipeline's timeout
stops waiting but cannot stop the thread, which finishes in the background and
writes nothing.

Datasets, checked in the Earth Engine catalog on 2026-11-14:
- ESA/WorldCover/v200 (2021, 10 m), band `Map`. Class 50 is built-up; 10 tree
  cover, 20 shrubland, 30 grassland, 40 cropland, 90 herbaceous wetland and 95
  mangroves are the "natural" reference. Covers Alaska, Hawaii and Puerto Rico.
- LANDSAT/LC08/C02/T1_L2 and LANDSAT/LC09/C02/T1_L2, band `ST_B10`. The image
  metadata gives kelvin = DN * 0.00341802 + 149.0.
- USGS/SRTMGL1_003 (SRTM, 30 m), band `elevation` in meters. Covers 60 N to
  56 S, so northern Alaska has no elevation or slope. Checked 2026-11-14.
- projects/gtac-data-publish/assets/TCC/Product_Version/2025-6, band
  `Science_Percent_Tree_Canopy_Cover` (0-100), study areas CONUS, AK, HI and
  PRUSVI. The older USGS/NLCD_RELEASES/2021_REL/TCC is deprecated.

Land cover, terrain and water (profile facts, never needs) ride in the same
request. Slope is computed on the DEM as resampled for the 240 m reduction, so
it understates short steep slopes; it describes the county, not a site.

Reducing at 240 m kept results within about 0.1 C of 120 m and cut the slowest
county (San Bernardino, CA) from 16 s to 10 s.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from shapely.geometry import mapping

from app.cache import DAY, cached_fetch
from app.config import Settings
from app.geo import County
from app.sources import SourceError

SOURCE_LABEL = (
    "Landsat 8/9 surface temperature (summer 2021-2025), ESA WorldCover 2021, "
    "USFS Tree Canopy Cover 2025, via Google Earth Engine"
)
LAND_SOURCE_LABEL = "ESA WorldCover 2021 and NASA SRTM elevation, via Google Earth Engine"

SUMMER_YEARS = (2021, 2025)
MAX_SCENE_CLOUD_PERCENT = 30
REDUCE_SCALE_M = 240
# Bump when the method changes so old cached values are not reused.
METHOD_VERSION = 2

# Open land on slopes under this many degrees counts as flat open land.
FLAT_SLOPE_DEGREES = 5

_TCC_ASSET = "projects/gtac-data-publish/assets/TCC/Product_Version/2025-6"
_TCC_AREA_BY_STATE = {"AK": "AK", "HI": "HI", "PR": "PRUSVI", "VI": "PRUSVI"}
_BUILT_UP = 50
_NATURAL = (10, 20, 30, 40, 90, 95)
# Profile shares of county area: name -> WorldCover classes.
_COVER = {
    "forest": (10,),
    "cropland": (40,),
    "grass_shrub": (20, 30),
    "water": (80,),
    "wetland": (90, 95),
}
_OPEN = (20, 30, 40, 60)  # shrubland, grassland, cropland, bare or sparse

_init_lock = threading.Lock()
_initialised: str | None = None


def _ee(project: str) -> Any:
    """The initialised `ee` module. Raises SourceError, never anything else."""
    global _initialised
    if not project:
        raise SourceError("EE_PROJECT is not set")
    try:
        import ee
    except ImportError as exc:
        raise SourceError("earthengine-api is not installed") from exc
    with _init_lock:
        if _initialised != project:
            try:
                ee.Initialize(project=project)
            except Exception as exc:
                raise SourceError(
                    f"Earth Engine sign-in failed ({type(exc).__name__}); "
                    "run `earthengine authenticate`"
                ) from exc
            _initialised = project
    return ee


def _round(value: Any, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _compute(project: str, county: County) -> dict[str, Any]:
    ee = _ee(project)
    region = ee.Geometry(mapping(county.geometry))

    land_cover = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")
    built_up = land_cover.eq(_BUILT_UP)
    natural = land_cover.remap(list(_NATURAL), [1] * len(_NATURAL), 0).eq(1)

    def surface_celsius(image: Any) -> Any:
        qa = image.select("QA_PIXEL")
        # Bits 1 dilated cloud, 3 cloud, 4 cloud shadow, 5 snow.
        clear = qa.bitwiseAnd((1 << 1) | (1 << 3) | (1 << 4) | (1 << 5)).eq(0)
        kelvin = image.select("ST_B10").multiply(0.00341802).add(149.0)
        return kelvin.subtract(273.15).rename("lst").updateMask(clear)

    first_year, last_year = SUMMER_YEARS
    scenes = None
    for collection in ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"):
        part = (
            ee.ImageCollection(collection)
            .filterBounds(region)
            .filter(ee.Filter.calendarRange(6, 8, "month"))
            .filter(ee.Filter.calendarRange(first_year, last_year, "year"))
            .filter(ee.Filter.lt("CLOUD_COVER", MAX_SCENE_CLOUD_PERCENT))
            .map(surface_celsius)
        )
        scenes = part if scenes is None else scenes.merge(part)
    summer = scenes.median()

    area = _TCC_AREA_BY_STATE.get(county.state, "CONUS")
    canopy = (
        ee.ImageCollection(_TCC_ASSET)
        .filter(ee.Filter.eq("study_area", area))
        .filter(ee.Filter.eq("year", 2025))
        .mosaic()
        .select("Science_Percent_Tree_Canopy_Cover")
    )

    def classes(codes: tuple[int, ...]) -> Any:
        return land_cover.remap(list(codes), [1] * len(codes), 0).eq(1)

    elevation = ee.Image("USGS/SRTMGL1_003").select("elevation")
    slope = ee.Terrain.slope(elevation)
    flat_open = classes(_OPEN).And(slope.lt(FLAT_SLOPE_DEGREES))

    stack = ee.Image.cat(
        [
            summer.updateMask(built_up).rename("lst_built_up"),
            summer.updateMask(natural).rename("lst_natural"),
            built_up.rename("built_up_share").toFloat(),
            canopy.updateMask(built_up).rename("canopy_built_up"),
            *(classes(codes).rename(f"{name}_share").toFloat() for name, codes in _COVER.items()),
            flat_open.rename("flat_open_share").toFloat(),
            slope.rename("slope_deg"),
        ]
    )
    reduce_args = {
        "geometry": region,
        "scale": REDUCE_SCALE_M,
        "bestEffort": True,
        "maxPixels": 1_000_000_000,
        "tileScale": 4,
    }
    means = stack.reduceRegion(ee.Reducer.mean(), **reduce_args)
    heights = elevation.reduceRegion(
        ee.Reducer.mean().combine(ee.Reducer.minMax(), sharedInputs=True), **reduce_args
    )
    try:
        # One round trip for both reductions.
        both = ee.Dictionary({"means": means, "heights": heights}).getInfo()
    except Exception as exc:
        raise SourceError(f"Earth Engine request failed ({type(exc).__name__})") from exc
    out, heights_m = both["means"], both["heights"]

    built, natural_t = out.get("lst_built_up"), out.get("lst_natural")
    canopy_pct = out.get("canopy_built_up")
    return {
        "heat_island_c": _round(built - natural_t, 2)
        if built is not None and natural_t is not None
        else None,
        "lst_built_up_c": _round(built, 1),
        "lst_natural_c": _round(natural_t, 1),
        "built_up_share": _round(out.get("built_up_share"), 4),
        # Percent in the source; stored as a share like the other needs.
        "canopy_built_up": _round(canopy_pct / 100, 4) if canopy_pct is not None else None,
        **{f"{name}_share": _round(out.get(f"{name}_share"), 4) for name in _COVER},
        "flat_open_share": _round(out.get("flat_open_share"), 4),
        "slope_deg": _round(out.get("slope_deg"), 2),
        "elevation_mean_m": _round(heights_m.get("elevation_mean"), 0),
        "elevation_min_m": _round(heights_m.get("elevation_min"), 0),
        "elevation_max_m": _round(heights_m.get("elevation_max"), 0),
    }


async def fetch_county_metrics(settings: Settings, county: County) -> tuple[dict[str, Any], bool]:
    """(metrics, came_from_cache). Values are None where Earth Engine had no
    usable pixels, such as built-up land masked by cloud all summer."""
    if not settings.ee_project:
        raise SourceError("EE_PROJECT is not set")

    async def fetch() -> dict[str, Any]:
        return await asyncio.to_thread(_compute, settings.ee_project, county)

    value, cached = await cached_fetch(
        settings.cache_dir,
        "earthengine",
        {"fips": county.fips, "v": METHOD_VERSION, "years": SUMMER_YEARS},
        settings.ee_cache_ttl_days * DAY,
        fetch,
    )
    return value, cached
