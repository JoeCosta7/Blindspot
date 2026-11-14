"""One-time download and conversion of the four national files.

    python scripts/prep_data.py                 # everything
    python scripts/prep_data.py counties nri    # only some steps

Raw downloads go to data/raw/ (git-ignored) and are reused on the next run.
Outputs: data/counties.geojson, repowering.parquet, lmop.parquet,
nri_counties.parquet.

Every column mapping below was written after opening the real file on
2026-11-13. Each step prints the columns it found and stops if an expected one
is missing, so a changed file fails loudly here instead of at request time.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"

# Census cartographic boundary file, 1:5,000,000. 2025 was the latest vintage
# on www2.census.gov/geo/tiger/ on 2026-11-13.
COUNTIES_URL = "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_county_5m.zip"

# Linked from https://www.epa.gov/re-powering/how-identify-sites
# "RE-Powering Screening Dataset (xlsx)", 2022 dataset updated 2023, ~82 MB.
REPOWERING_URL = (
    "https://www.epa.gov/system/files/documents/2023-05/"
    "re-powering-screening-dataset-2022%20Updated.xlsx"
)

# Linked from https://www.epa.gov/lmop/lmop-landfill-and-project-database
# "Landfill- and project-level data combined", September 2024.
LMOP_URL = "https://www.epa.gov/system/files/documents/2024-09/lmopcompositedata.xlsx"

# FEMA retired hazards.fema.gov/nri in 2026 (it redirects to the RAPT page) and
# the zip downloads on fema.gov reject scripted clients. FEMA's own ArcGIS
# feature service carries the same v1.20 county table and can be queried.
# Manual alternative: https://www.fema.gov/about/openfema/data-sets/national-risk-index-data
NRI_SERVICE_URL = (
    "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
    "National_Risk_Index_Counties/FeatureServer/0/query"
)

# ACS 5-year has no data for these island areas, so they are left out.
EXCLUDED_STATES = {"AS", "GU", "MP", "VI"}

USER_AGENT = "county-sustainability-planner/0.1 (data prep)"


# --- helpers ----------------------------------------------------------------


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  using existing {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream(
        "GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=300
    ) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
    tmp.rename(dest)
    print(f"  saved {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """The EPA headers contain non-breaking and doubled spaces."""
    df.columns = [" ".join(str(c).replace("\xa0", " ").split()) for c in df.columns]
    return df


def require_columns(df: pd.DataFrame, expected: list[str], what: str) -> None:
    missing = [c for c in expected if c not in df.columns]
    if missing:
        sys.exit(
            f"{what}: expected columns are missing: {missing}\n"
            f"Columns in the file: {list(df.columns)}\n"
            "Fix the mapping in prep_data.py."
        )


def assign_county_fips(
    df: pd.DataFrame, counties: gpd.GeoDataFrame, lat: str = "lat", lon: str = "lon"
) -> pd.Series:
    """County FIPS for each row by point-in-polygon, then nearest county within
    about 5 km for points that fall just off a generalized coastline."""
    has_xy = df[lat].notna() & df[lon].notna()
    points = gpd.GeoDataFrame(
        df.loc[has_xy, []],
        geometry=gpd.points_from_xy(df.loc[has_xy, lon], df.loc[has_xy, lat]),
        crs="EPSG:4326",
    )
    polys = counties[["GEOID", "geometry"]]
    joined = gpd.sjoin(points, polys, predicate="within", how="left")
    fips = joined.groupby(level=0)["GEOID"].first()

    unmatched = fips[fips.isna()].index
    if len(unmatched):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)  # degrees are fine at 5 km
            near = gpd.sjoin_nearest(points.loc[unmatched], polys, how="left", max_distance=0.05)
        fips.loc[unmatched] = near.groupby(level=0)["GEOID"].first()

    return fips.reindex(df.index)


def yes(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.upper().eq("Y").fillna(False)


# --- counties ---------------------------------------------------------------


def prep_counties() -> gpd.GeoDataFrame:
    print("[counties]")
    src = download(COUNTIES_URL, RAW_DIR / "cb_2025_us_county_5m.zip")
    gdf = gpd.read_file(f"zip://{src}")
    print(f"  columns: {list(gdf.columns)}")
    require_columns(gdf, ["GEOID", "NAME", "NAMELSAD", "STUSPS", "STATEFP", "ALAND"], "counties")
    gdf = gdf[~gdf["STUSPS"].isin(EXCLUDED_STATES)].to_crs("EPSG:4326")

    # Centroids in an equal-area projection, converted back to lon/lat.
    centroids = gdf.geometry.to_crs("EPSG:6933").centroid.to_crs("EPSG:4326")
    out = gpd.GeoDataFrame(
        {
            "GEOID": gdf["GEOID"],
            "NAME": gdf["NAME"],
            "NAMELSAD": gdf["NAMELSAD"],
            "STUSPS": gdf["STUSPS"],
            "STATEFP": gdf["STATEFP"],
            "centroid_lon": centroids.x.round(4),
            "centroid_lat": centroids.y.round(4),
            "land_sq_km": (gdf["ALAND"] / 1e6).round(1),
        },
        geometry=gdf.geometry,
        crs="EPSG:4326",
    ).sort_values("GEOID")

    dest = DATA_DIR / "counties.geojson"
    dest.unlink(missing_ok=True)
    out.to_file(dest, driver="GeoJSON", COORDINATE_PRECISION=4)
    print(f"  wrote {dest.name}: {len(out)} counties, {dest.stat().st_size / 1e6:.1f} MB")
    return out


def load_counties() -> gpd.GeoDataFrame:
    path = DATA_DIR / "counties.geojson"
    if not path.exists():
        return prep_counties()
    return gpd.read_file(path)


# --- RE-Powering ------------------------------------------------------------

REPOWERING_COLUMNS = [
    "Cross-Reference Number",
    "State",
    "Program",
    "Site Name",
    "City",
    "County",
    "Acreage (Acres)",
    "Known Landfill",
    "Known Abandoned Mine Land",
    "RE-Powering Profile",
    "Estimated PV Capacity (MW)",
    "Utility Scale PV",
    "Distributed Scale PV",
    "Off-grid PV",
    "No Acreage PV",
    "Estimated Wind Energy Capacity (MW)",
    "Utility Scale Wind",
    "Community Scale Wind",
    "Facility Scale Wind",
    "No Acreage Wind",
    "Distance to Nearest Transmission Line (miles)",
    "Latitude",
    "Longitude",
]


def first_flag(df: pd.DataFrame, columns: dict[str, str]) -> pd.Series:
    """Name of the first screening column marked Y, else null."""
    result = pd.Series(pd.NA, index=df.index, dtype="string")
    for column, label in reversed(columns.items()):
        result = result.mask(yes(df[column]), label)
    return result


def prep_repowering(counties: gpd.GeoDataFrame) -> None:
    print("[repowering]")
    src = download(REPOWERING_URL, RAW_DIR / "repowering.xlsx")
    print("  reading the workbook (about a minute)")
    df = clean_columns(pd.read_excel(src, sheet_name="RE-Powering Sites"))
    print(f"  columns: {list(df.columns)}")
    require_columns(df, REPOWERING_COLUMNS, "RE-Powering")

    program = df["Program"].astype("string").str.strip()
    is_mine = yes(df["Known Abandoned Mine Land"])
    is_landfill = yes(df["Known Landfill"])
    is_brownfield = program.str.contains("BROWNFIELD", case=False, na=False)
    # Mine and landfill flags win because they describe the land itself. A site
    # is called a brownfield only when it comes from a brownfields program;
    # Superfund, RCRA and state cleanup sites are "contaminated_site" because
    # brownfields funding does not generally cover them.
    site_type = pd.Series("contaminated_site", index=df.index, dtype="string")
    site_type = site_type.mask(is_brownfield, "brownfield")
    site_type = site_type.mask(is_landfill, "landfill")
    site_type = site_type.mask(is_mine, "mine_land")

    out = pd.DataFrame(
        {
            "site_id": df["Cross-Reference Number"].astype("int64"),
            "name": df["Site Name"].astype("string").str.strip(),
            "site_type": site_type,
            "program": program,
            "city": df["City"].astype("string").str.strip(),
            "county_name": df["County"].astype("string").str.strip(),
            "state": df["State"].astype("string").str.strip(),
            "lat": pd.to_numeric(df["Latitude"], errors="coerce"),
            "lon": pd.to_numeric(df["Longitude"], errors="coerce"),
            "acres": pd.to_numeric(df["Acreage (Acres)"], errors="coerce"),
            "epa_pv_mw": pd.to_numeric(df["Estimated PV Capacity (MW)"], errors="coerce"),
            "epa_wind_mw": pd.to_numeric(
                df["Estimated Wind Energy Capacity (MW)"], errors="coerce"
            ),
            "pv_scale": first_flag(
                df,
                {
                    "Utility Scale PV": "utility",
                    "Distributed Scale PV": "distributed",
                    "Off-grid PV": "off_grid",
                    "No Acreage PV": "unknown_acreage",
                },
            ),
            "wind_scale": first_flag(
                df,
                {
                    "Utility Scale Wind": "utility",
                    "Community Scale Wind": "community",
                    "Facility Scale Wind": "facility",
                    "No Acreage Wind": "unknown_acreage",
                },
            ),
            "transmission_miles": pd.to_numeric(
                df["Distance to Nearest Transmission Line (miles)"], errors="coerce"
            ),
            "profile_url": df["RE-Powering Profile"].astype("string"),
        }
    )
    out["solar_flag"] = out["pv_scale"].notna()
    out["wind_flag"] = out["wind_scale"].notna()

    print("  assigning county FIPS by location (the file has county names only)")
    out["county_fips"] = assign_county_fips(out, counties)
    dropped = int(out["county_fips"].isna().sum())
    out = out[out["county_fips"].notna()].reset_index(drop=True)

    dest = DATA_DIR / "repowering.parquet"
    out.to_parquet(dest, index=False)
    print(f"  site types:\n{out['site_type'].value_counts().to_string()}")
    print(
        f"  wrote {dest.name}: {len(out)} sites in {out['county_fips'].nunique()} counties, "
        f"{dest.stat().st_size / 1e6:.1f} MB ({dropped} dropped: outside every county)"
    )


# --- LMOP -------------------------------------------------------------------

LMOP_COLUMNS = [
    "Landfill ID",
    "Landfill Name",
    "State",
    "City",
    "County",
    "Latitude",
    "Longitude",
    "Ownership Type",
    "Landfill Closure Year",
    "Current Landfill Status",
    "Waste in Place (tons)",
    "LFG Generated (mmscfd)",
    "LFG Collection System In Place?",
    "LFG Collected (mmscfd)",
    "LFG Collected Year",
    "Current Project Status",
    "Rated MW Capacity",
]

# Values of "Current Project Status", as defined on the workbook's Summary
# sheet, from most to least advanced.
LMOP_STATUS_ORDER = [
    "Operational",
    "Construction",
    "Planned",
    "Candidate",
    "Future Potential",
    "Shutdown",
    "Low Potential",
    "Unknown",
]

COUNTY_SUFFIXES = (
    " city and borough",
    " census area",
    " municipality",
    " municipio",
    " borough",
    " county",
    " parish",
)


def county_key(name: object) -> str:
    text = str(name).lower().strip()
    for suffix in COUNTY_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return "".join(ch for ch in text if ch.isalnum())


def prep_lmop(counties: gpd.GeoDataFrame) -> None:
    print("[lmop]")
    src = download(LMOP_URL, RAW_DIR / "lmopcompositedata.xlsx")
    df = clean_columns(pd.read_excel(src, sheet_name="LMOP Database"))
    print(f"  columns: {list(df.columns)}")
    require_columns(df, LMOP_COLUMNS, "LMOP")

    statuses = set(df["Current Project Status"].dropna().unique())
    unknown_statuses = statuses - set(LMOP_STATUS_ORDER)
    if unknown_statuses:
        sys.exit(f"LMOP: unrecognized project status values: {sorted(unknown_statuses)}")

    rank = {status: i for i, status in enumerate(LMOP_STATUS_ORDER)}
    df["_rank"] = df["Current Project Status"].map(rank).fillna(len(rank))
    df["_operational_mw"] = pd.to_numeric(df["Rated MW Capacity"], errors="coerce").where(
        df["Current Project Status"].eq("Operational")
    )

    # The file has one row per landfill-project pair. Landfill fields repeat on
    # every row, so "first" is safe for them.
    grouped = df.sort_values("_rank").groupby("Landfill ID", sort=True)
    out = grouped.agg(
        name=("Landfill Name", "first"),
        state=("State", "first"),
        city=("City", "first"),
        county_name=("County", "first"),
        lat=("Latitude", "first"),
        lon=("Longitude", "first"),
        ownership_type=("Ownership Type", "first"),
        status=("Current Landfill Status", "first"),
        closure_year=("Landfill Closure Year", "first"),
        waste_in_place_tons=("Waste in Place (tons)", "first"),
        lfg_generated_mmscfd=("LFG Generated (mmscfd)", "first"),
        lfg_collected_mmscfd=("LFG Collected (mmscfd)", "first"),
        lfg_collected_year=("LFG Collected Year", "first"),
        collection_system=("LFG Collection System In Place?", "first"),
        project_status=("Current Project Status", "first"),  # most advanced
        is_candidate=("Current Project Status", lambda s: bool(s.eq("Candidate").any())),
        has_operational_project=(
            "Current Project Status",
            lambda s: bool(s.eq("Operational").any()),
        ),
        operational_mw=("_operational_mw", lambda s: s.sum(min_count=1)),
    ).reset_index()
    out = out.rename(columns={"Landfill ID": "landfill_id"})
    for column in (
        "lat",
        "lon",
        "closure_year",
        "waste_in_place_tons",
        "lfg_generated_mmscfd",
        "lfg_collected_mmscfd",
        "lfg_collected_year",
        "operational_mw",
    ):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    for column in (
        "name",
        "state",
        "city",
        "county_name",
        "ownership_type",
        "status",
        "collection_system",
        "project_status",
    ):
        out[column] = out[column].astype("string").str.strip()

    both = int((out["is_candidate"] & out["has_operational_project"]).sum())
    print(f"  landfills that are both Candidate and Operational: {both}")

    out["county_fips"] = assign_county_fips(out, counties)
    # Landfills with no coordinates: fall back to the county name.
    lookup = {
        (state, county_key(name)): geoid
        for geoid, name, state in zip(
            counties["GEOID"], counties["NAME"], counties["STUSPS"], strict=True
        )
    }
    missing = out["county_fips"].isna()
    out.loc[missing, "county_fips"] = [
        lookup.get((state, county_key(name)))
        for state, name in zip(
            out.loc[missing, "state"], out.loc[missing, "county_name"], strict=True
        )
    ]
    by_name = int(missing.sum() - out["county_fips"].isna().sum())
    dropped = int(out["county_fips"].isna().sum())
    out = out[out["county_fips"].notna()].reset_index(drop=True)

    dest = DATA_DIR / "lmop.parquet"
    out.to_parquet(dest, index=False)
    print(f"  project status:\n{out['project_status'].value_counts().to_string()}")
    print(
        f"  wrote {dest.name}: {len(out)} landfills, {int(out['is_candidate'].sum())} "
        f"candidates, {int(out['lat'].isna().sum())} without coordinates "
        f"({by_name} placed by county name, {dropped} dropped: no county match)"
    )


# --- NRI --------------------------------------------------------------------

# Field names read from the service's layer definition on 2026-11-13. Version
# 1.20 renamed riverine flooding (RFLD) to inland flooding (IFLD).
NRI_FIELDS = {
    "STCOFIPS": "county_fips",
    "COUNTY": "county_name",
    "STATEABBRV": "state",
    "NRI_VER": "nri_version",
    "RISK_RATNG": "risk_rating",
    "IFLD_RISKR": "inland_flood_rating",
    "WFIR_RISKR": "wildfire_rating",
    "DRGT_RISKR": "drought_rating",
    "HWAV_RISKR": "heat_wave_rating",
}


def prep_nri() -> None:
    print("[nri]")
    raw_path = RAW_DIR / "nri_counties.json"
    if raw_path.exists():
        print(f"  using existing {raw_path.name}")
        rows = json.loads(raw_path.read_text())
    else:
        rows = []
        page = 2000  # the service's maxRecordCount
        with httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}) as client:
            while True:
                resp = client.get(
                    NRI_SERVICE_URL,
                    params={
                        "where": "1=1",
                        "outFields": ",".join(NRI_FIELDS),
                        "returnGeometry": "false",
                        "orderByFields": "OBJECTID",
                        "resultOffset": len(rows),
                        "resultRecordCount": page,
                        "f": "json",
                    },
                )
                resp.raise_for_status()
                body = resp.json()
                if "error" in body:
                    sys.exit(f"NRI service error: {body['error']}")
                features = body.get("features", [])
                rows.extend(f["attributes"] for f in features)
                print(f"  fetched {len(rows)} rows")
                if len(features) < page:
                    break
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(rows))

    df = pd.DataFrame(rows)
    print(f"  columns: {list(df.columns)}")
    require_columns(df, list(NRI_FIELDS), "NRI")
    out = df[list(NRI_FIELDS)].rename(columns=NRI_FIELDS).astype("string")
    out["county_fips"] = out["county_fips"].str.zfill(5)
    out = out.drop_duplicates("county_fips").sort_values("county_fips")

    dest = DATA_DIR / "nri_counties.parquet"
    out.to_parquet(dest, index=False)
    print(f"  versions: {sorted(out['nri_version'].dropna().unique())}")
    print(f"  wildfire ratings: {sorted(out['wildfire_rating'].dropna().unique())}")
    print(f"  wrote {dest.name}: {len(out)} counties")


# --- entry point ------------------------------------------------------------

STEPS = ("counties", "repowering", "lmop", "nri")


def main(argv: list[str]) -> None:
    steps = argv or list(STEPS)
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        sys.exit(f"Unknown step(s) {unknown}. Choose from {STEPS}.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    counties = prep_counties() if "counties" in steps else None
    if "repowering" in steps or "lmop" in steps:
        counties = counties if counties is not None else load_counties()
    if "repowering" in steps:
        prep_repowering(counties)
    if "lmop" in steps:
        prep_lmop(counties)
    if "nri" in steps:
        prep_nri()
    print("done")


if __name__ == "__main__":
    main(sys.argv[1:])
