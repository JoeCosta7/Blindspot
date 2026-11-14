"""One-time download and conversion of the national files.

    python scripts/prep_data.py                 # everything
    python scripts/prep_data.py counties nri    # only some steps

Raw downloads go to data/raw/ (git-ignored) and are reused on the next run.
Outputs: data/counties.geojson, repowering.parquet, lmop.parquet,
nri_counties.parquet, and the stretch files state_energy.json, dams.parquet
farms.parquet and svi_counties.parquet. The `farms` step downloads about 310 MB.

Steps added 2026-11-14: rucc, lead (energy_burden.parquet; downloads about
3 GB of state zips and keeps only the county CSVs, about 670 MB),
energy_communities, eia860 (power_plants.parquet), eia861
(utilities.parquet), places (places_counties.parquet), air
(air_quality.parquet) and wildfire (wildfire_counties.parquet, 62 MB zip).

Every column mapping below was written after opening the real file on
2026-11-13 or 2026-11-14. Each step prints the columns it found and stops if an
expected one is missing, so a changed file fails loudly here instead of at
request time.
"""

from __future__ import annotations

import json
import re
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

# --- stretch sources, all read on 2026-11-14 ---------------------------------

# EIA "Electric Sales, Revenue, and Average Price", Table 5A: residential
# average monthly bill by state. Linked from
# https://www.eia.gov/electricity/sales_revenue_price/ (data for 2024).
# The older https://www.eia.gov/electricity/data/state/avgprice_annual.xlsx
# stops at 2020, so it is not used.
EIA_BILL_URL = "https://www.eia.gov/electricity/sales_revenue_price/xls/table_5A.xlsx"

# EPA eGRID2023 (rev 2), linked from https://www.epa.gov/egrid/detailed-data.
EGRID_URL = "https://www.epa.gov/system/files/documents/2025-06/egrid2023_data_rev2.xlsx"

# ORNL HydroSource, "Technical Potential for Hydropower Capacity at Nonpowered
# Dams" (Hansen et al., 2025): 2,616 dams in the lower 48 states.
# https://hydrosource.ornl.gov/data/datasets/hydropower-capacity-us-npd/
NPD_URL = (
    "https://hydrosource.s3.us-east-2.amazonaws.com/files/data/datasets/"
    "hydropower-capacity-us-npd/TechPotentialNPDs.csv"
)

# USDA NASS Quick Stats bulk file for the 2022 Census of Agriculture, linked
# from https://www.nass.usda.gov/datasets. No key needed (the Quick Stats API
# does need one). About 310 MB compressed.
NASS_CENSUS_URL = "https://www.nass.usda.gov/sites/default/files/datasets/qs.census2022.txt.gz"

# EPA AgSTAR livestock anaerobic digester database, linked from
# https://www.epa.gov/agstar/livestock-anaerobic-digester-database
AGSTAR_URL = "https://www.epa.gov/sites/default/files/2020-10/agstar-livestock-ad-database.xlsx"

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


def plain_key(name: object) -> str:
    """county_key without accents, so "Dona Ana" matches "Doña Ana"."""
    import unicodedata

    text = unicodedata.normalize("NFKD", str(name))
    return county_key("".join(ch for ch in text if not unicodedata.combining(ch)))


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


# --- state electricity bills and grid carbon ---------------------------------

EIA_BILL_COLUMNS = [
    "State",
    "Number of Customers",
    "Average Monthly Consumption (kWh)",
    "Average Price (cents/kWh)",
    "Average Monthly Bill (Dollar and cents)",
]


def state_abbreviations() -> dict[str, str]:
    """State name -> postal abbreviation, from the county boundary file."""
    src = download(COUNTIES_URL, RAW_DIR / "cb_2025_us_county_5m.zip")
    table = gpd.read_file(f"zip://{src}", ignore_geometry=True)
    require_columns(table, ["STATE_NAME", "STUSPS"], "counties")
    return dict(zip(table["STATE_NAME"], table["STUSPS"], strict=True))


def prep_energy() -> None:
    print("[energy]")
    abbreviations = state_abbreviations()

    # EIA: row 0 is the title with the data year, row 2 the header.
    src = download(EIA_BILL_URL, RAW_DIR / "eia_table_5A.xlsx")
    raw = pd.read_excel(src, header=None)
    title = str(raw.iat[0, 0])
    year_match = re.match(r"\s*(\d{4}) Average Monthly Bill", title)
    if not year_match:
        sys.exit(f"EIA table 5A: unexpected title {title!r}")
    bill_year = int(year_match.group(1))
    bills = raw.iloc[3:].copy()
    bills.columns = [str(c).strip() for c in raw.iloc[2]]
    print(f"  EIA columns: {list(bills.columns)} ({title.strip()})")
    require_columns(bills, EIA_BILL_COLUMNS, "EIA table 5A")
    names = bills["State"].astype("string").str.strip()
    bills["abbr"] = names.map(abbreviations).mask(names.eq("U.S. Total"), "US")
    bills = bills[bills["abbr"].notna()]  # drops the Census division subtotals

    # eGRID: sheets are named ST<yy> and US<yy>; row 0 is descriptions, row 1 codes.
    src = download(EGRID_URL, RAW_DIR / "egrid2023_data_rev2.xlsx")
    sheets = pd.ExcelFile(src).sheet_names
    state_sheet = next((n for n in sheets if re.fullmatch(r"ST\d\d", n)), None)
    us_sheet = next((n for n in sheets if re.fullmatch(r"US\d\d", n)), None)
    if not state_sheet or not us_sheet:
        sys.exit(f"eGRID: state or US sheet not found in {sheets}")
    grid = pd.read_excel(src, sheet_name=state_sheet, header=1)
    grid_us = pd.read_excel(src, sheet_name=us_sheet, header=1)
    print(f"  eGRID {state_sheet}: {len(grid)} rows; {us_sheet}: {len(grid_us)} row")
    require_columns(grid, ["YEAR", "PSTATABB", "STCO2RTA"], "eGRID state sheet")
    require_columns(grid_us, ["YEAR", "USCO2RTA"], "eGRID US sheet")
    grid_year = int(grid_us["YEAR"].iloc[0])

    states: dict[str, dict[str, float | None]] = {}
    for row in bills.itertuples(index=False):
        states[row.abbr] = {
            "residential_monthly_bill_usd": round(float(row[4]), 2),
            "residential_price_cents_kwh": round(float(row[3]), 2),
            "residential_monthly_kwh": round(float(row[2])),
        }
    for abbr, rate in zip(grid["PSTATABB"], grid["STCO2RTA"], strict=True):
        states.setdefault(str(abbr), {})["grid_co2_lb_per_mwh"] = round(float(rate), 1)
    us_rate = float(grid_us["USCO2RTA"].iloc[0])
    states.setdefault("US", {})["grid_co2_lb_per_mwh"] = round(us_rate, 1)

    with_bill = sum("residential_monthly_bill_usd" in v for v in states.values())
    with_grid = sum("grid_co2_lb_per_mwh" in v for v in states.values())
    if with_bill < 52 or "residential_monthly_bill_usd" not in states.get("US", {}):
        sys.exit(f"EIA table 5A: expected 50 states, DC and the US total; matched {with_bill}")

    dest = DATA_DIR / "state_energy.json"
    dest.write_text(
        json.dumps(
            {"electricity_year": bill_year, "grid_year": grid_year, "states": states},
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    print(
        f"  wrote {dest.name}: {with_bill} areas with {bill_year} bills, "
        f"{with_grid} with {grid_year} CO2 rates; US: {states['US']}"
    )


# --- non-powered dams --------------------------------------------------------

NPD_COLUMNS = [
    "nididfull",
    "lat",
    "long",
    "dam_name",
    "owner_type",
    "prps",
    "city",
    "state_abbr",
    "waterway",
    "inputdata",
    "cap_mw",
    "gen_mwh_yr",
    "cf_yr",
    "head_ft_yr",
]


def prep_dams(counties: gpd.GeoDataFrame) -> None:
    print("[dams]")
    src = download(NPD_URL, RAW_DIR / "TechPotentialNPDs.csv")
    df = pd.read_csv(src)
    print(f"  columns: {list(df.columns)[:20]} ... ({len(df.columns)} in all)")
    require_columns(df, NPD_COLUMNS, "ORNL non-powered dams")

    out = pd.DataFrame(
        {
            "dam_id": df["nididfull"].astype("string").str.strip(),
            "name": df["dam_name"].astype("string").str.strip(),
            "owner_type": df["owner_type"].astype("string").str.strip(),
            "purposes": df["prps"].astype("string").str.strip(),
            "waterway": df["waterway"].astype("string").str.strip(),
            "city": df["city"].astype("string").str.strip(),
            "state": df["state_abbr"].astype("string").str.strip(),
            "lat": pd.to_numeric(df["lat"], errors="coerce"),
            "lon": pd.to_numeric(df["long"], errors="coerce"),
            "capacity_mw": pd.to_numeric(df["cap_mw"], errors="coerce"),
            "generation_mwh_yr": pd.to_numeric(df["gen_mwh_yr"], errors="coerce"),
            "capacity_factor": pd.to_numeric(df["cf_yr"], errors="coerce"),
            "head_ft": pd.to_numeric(df["head_ft_yr"], errors="coerce").round(1),
            "input_data": df["inputdata"].astype("string").str.strip(),
        }
    )
    out["county_fips"] = assign_county_fips(out, counties)
    dropped = int(out["county_fips"].isna().sum())
    out = out[out["county_fips"].notna()].reset_index(drop=True)

    dest = DATA_DIR / "dams.parquet"
    out.to_parquet(dest, index=False)
    print(
        f"  wrote {dest.name}: {len(out)} dams in {out['county_fips'].nunique()} counties, "
        f"{out['capacity_mw'].sum():,.0f} MW in all ({dropped} dropped: outside every county)"
    )


# --- farms: Census of Agriculture and AgSTAR digesters ------------------------

NASS_COLUMNS = [
    "SHORT_DESC",
    "DOMAIN_DESC",
    "DOMAINCAT_DESC",
    "AGG_LEVEL_DESC",
    "STATE_FIPS_CODE",
    "COUNTY_ANSI",
    "YEAR",
    "VALUE",
]

# (SHORT_DESC, DOMAINCAT_DESC) -> our column. Read from the file itself. At
# county level the largest published herd classes are 500 or more milk cows
# and 1,000 or more hogs.
NASS_ITEMS = {
    ("FARM OPERATIONS - ACRES OPERATED", "NOT SPECIFIED"): "land_in_farms_acres",
    ("AG LAND, CROPLAND - ACRES", "NOT SPECIFIED"): "cropland_acres",
    (
        "CATTLE, COWS, MILK - OPERATIONS WITH INVENTORY",
        "INVENTORY OF MILK COWS: (500 OR MORE HEAD)",
    ): "dairy_farms_500_plus",
    (
        "HOGS - OPERATIONS WITH INVENTORY",
        "INVENTORY OF HOGS: (1,000 OR MORE HEAD)",
    ): "hog_farms_1000_plus",
}

AGSTAR_COLUMNS = ["Project Name", "County", "State", "Status"]

# Misspelled county names in the AgSTAR file as downloaded on 2026-11-14. Only
# the spelling is corrected: the project stays in the county EPA names, even
# where the listed city suggests a neighbouring one.
AGSTAR_COUNTY_SPELLING = {
    ("IL", "caroll"): "carroll",
    ("NY", "niagra"): "niagara",
    ("NY", "rensselear"): "rensselaer",
    ("PA", "schuykill"): "schuylkill",
    ("SC", "darlingon"): "darlington",
    ("VT", "frankin"): "franklin",
    ("WI", "mantiowoc"): "manitowoc",
    ("WI", "oronto"): "oconto",
    ("WI", "saintcroix"): "stcroix",
}


def agstar_county_key(state: object, county: object) -> tuple[str, str]:
    key = (str(state).strip(), county_key(county))
    return key[0], AGSTAR_COUNTY_SPELLING.get(key, key[1])


def read_nass_counties(src: Path) -> pd.DataFrame:
    """One row per county with the NASS_ITEMS columns. Streams the file: it is
    several GB uncompressed."""
    header = pd.read_csv(src, sep="\t", nrows=0)
    print(f"  NASS columns: {list(header.columns)}")
    require_columns(header, NASS_COLUMNS, "NASS census file")
    wanted_descs = {short for short, _ in NASS_ITEMS}
    kept = []
    reader = pd.read_csv(
        src, sep="\t", usecols=NASS_COLUMNS, dtype="string", chunksize=500_000, quoting=3
    )
    for chunk in reader:
        chunk = chunk[chunk["AGG_LEVEL_DESC"].eq("COUNTY") & chunk["SHORT_DESC"].isin(wanted_descs)]
        if len(chunk):
            kept.append(chunk)
    rows = pd.concat(kept, ignore_index=True)
    keys = list(zip(rows["SHORT_DESC"], rows["DOMAINCAT_DESC"], strict=True))
    rows["item"] = [NASS_ITEMS.get(key) for key in keys]
    rows = rows[rows["item"].notna() & rows["COUNTY_ANSI"].notna()]
    years = sorted(rows["YEAR"].unique())
    if years != ["2022"]:
        sys.exit(f"NASS census file: expected only 2022 rows, found {years}")
    rows["county_fips"] = rows["STATE_FIPS_CODE"].str.zfill(2) + rows["COUNTY_ANSI"].str.zfill(3)
    # "(D)" is withheld to avoid disclosing one farm; it becomes null, not zero.
    rows["value"] = pd.to_numeric(rows["VALUE"].str.replace(",", ""), errors="coerce")
    if rows.duplicated(["county_fips", "item"]).any():
        sys.exit("NASS census file: more than one row for a county and item")
    return rows.pivot(index="county_fips", columns="item", values="value").reset_index()


def prep_farms(counties: gpd.GeoDataFrame) -> None:
    print("[farms]")
    src = download(NASS_CENSUS_URL, RAW_DIR / "qs.census2022.txt.gz")
    print("  reading the census file (a few minutes)")
    farms = read_nass_counties(src)
    # A herd class with no farms has no row, so a missing count is zero for a
    # county the census covers.
    for column in ("dairy_farms_500_plus", "hog_farms_1000_plus"):
        farms[column] = farms[column].fillna(0).astype("int64")

    src = download(AGSTAR_URL, RAW_DIR / "agstar-livestock-ad-database.xlsx")
    digesters = clean_columns(pd.read_excel(src, sheet_name="Operational and Construction"))
    print(f"  AgSTAR columns: {list(digesters.columns)}")
    require_columns(digesters, AGSTAR_COLUMNS, "AgSTAR")
    statuses = set(digesters["Status"].dropna().unique())
    if not statuses <= {"Operational", "Construction"}:
        sys.exit(f"AgSTAR: unrecognized status values: {sorted(statuses)}")
    lookup = {
        (state, county_key(name)): geoid
        for geoid, name, state in zip(
            counties["GEOID"], counties["NAME"], counties["STUSPS"], strict=True
        )
    }
    digesters["county_fips"] = [
        lookup.get(agstar_county_key(state, name))
        for state, name in zip(digesters["State"], digesters["County"], strict=True)
    ]
    unplaced = digesters[digesters["county_fips"].isna()]
    if len(unplaced):
        print(f"  {len(unplaced)} AgSTAR projects not matched to a county:")
        print(unplaced[["Project Name", "County", "State"]].to_string(index=False))
    counts = (
        digesters.dropna(subset=["county_fips"])
        .groupby(["county_fips", "Status"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["Operational", "Construction"], fill_value=0)
        .rename(
            columns={
                "Operational": "digesters_operational",
                "Construction": "digesters_under_construction",
            }
        )
        .reset_index()
    )

    out = farms.merge(counts, on="county_fips", how="left")
    for column in ("digesters_operational", "digesters_under_construction"):
        out[column] = out[column].fillna(0).astype("int64")
    known = set(counties["GEOID"])
    unmatched = out[~out["county_fips"].isin(known)]
    out = out[out["county_fips"].isin(known)].sort_values("county_fips").reset_index(drop=True)

    dest = DATA_DIR / "farms.parquet"
    out.to_parquet(dest, index=False)
    print(
        f"  wrote {dest.name}: {len(out)} counties; "
        f"{int((out['dairy_farms_500_plus'] > 0).sum())} with a 500-cow dairy, "
        f"{int((out['hog_farms_1000_plus'] > 0).sum())} with a 1,000-hog farm, "
        f"{int((out['digesters_operational'] > 0).sum())} with an operating digester"
    )
    if len(unmatched):
        print(
            f"  {len(unmatched)} census counties are not in the 2025 boundary file "
            f"(states: {sorted(unmatched['county_fips'].str[:2].unique())}) and were dropped"
        )


# --- entry point ------------------------------------------------------------

SVI_URL = "https://svi.cdc.gov/Documents/Data/2022/csv/states_counties/SVI_2022_US_county.csv"
# Original column -> prepared column. Checked against the downloaded file and
# https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html
# on 2026-11-14. RPL_* are percentile ranks among all US counties (0-1, higher
# is more vulnerable); EP_* are percentages. -999 means no data.
SVI_FIELDS = {
    "FIPS": "county_fips",
    "RPL_THEMES": "svi_overall",
    "RPL_THEME1": "svi_socioeconomic",
    "RPL_THEME2": "svi_household",
    "RPL_THEME3": "svi_minority_language",
    "RPL_THEME4": "svi_housing_transport",
    "EP_AGE65": "age65_pct",
    "EP_DISABL": "disability_pct",
}


def prep_svi() -> None:
    print("[svi]")
    src = download(SVI_URL, RAW_DIR / "SVI_2022_US_county.csv")
    df = pd.read_csv(src, dtype={"FIPS": str}, encoding="utf-8-sig")
    print(f"  columns: {list(df.columns)}")
    require_columns(df, list(SVI_FIELDS), "SVI")
    out = df[list(SVI_FIELDS)].rename(columns=SVI_FIELDS)
    out["county_fips"] = out["county_fips"].str.zfill(5)
    values = [c for c in out.columns if c != "county_fips"]
    out[values] = out[values].astype(float).mask(out[values] < 0)  # -999 -> null
    out = out.drop_duplicates("county_fips").sort_values("county_fips")
    dest = DATA_DIR / "svi_counties.parquet"
    out.to_parquet(dest, index=False)
    print(f"  {len(out)} counties, {out['svi_overall'].isna().sum()} without an overall rank")
    print(f"  wrote {dest}")


# --- sources added 2026-11-14; every URL and column below was opened that day ---

# USDA ERS Rural-Urban Continuum Codes 2023, long format (one row per county
# and attribute). Linked from https://www.ers.usda.gov/data-products/rural-urban-continuum-codes
RUCC_URL = "https://www.ers.usda.gov/media/5768/2023-rural-urban-continuum-codes.csv"
RUCC_ATTRIBUTES = {
    "RUCC_2023": "rucc_code",
    "Description": "rucc_description",
    "Population_2020": "population_2020",
}


def prep_rucc() -> None:
    print("[rucc]")
    src = download(RUCC_URL, RAW_DIR / "rucc_2023.csv")
    df = pd.read_csv(src, dtype=str, encoding="latin-1")
    print(f"  columns: {list(df.columns)}")
    require_columns(df, ["FIPS", "Attribute", "Value"], "RUCC")
    found = set(df["Attribute"].unique())
    missing = set(RUCC_ATTRIBUTES) - found
    if missing:
        sys.exit(f"RUCC: attributes {sorted(missing)} not found; file has {sorted(found)}")
    wide = (
        df[df["Attribute"].isin(RUCC_ATTRIBUTES)]
        .pivot_table(index="FIPS", columns="Attribute", values="Value", aggfunc="first")
        .rename(columns=RUCC_ATTRIBUTES)
        .reset_index()
    )
    out = pd.DataFrame(
        {
            "county_fips": wide["FIPS"].str.zfill(5),
            "rucc_code": pd.to_numeric(wide["rucc_code"], errors="coerce").astype("Int64"),
            "rucc_description": wide["rucc_description"].astype("string").str.strip(),
            "population_2020": pd.to_numeric(
                wide["population_2020"].str.replace(",", ""), errors="coerce"
            ).astype("Int64"),
        }
    ).sort_values("county_fips")
    out = out[out["rucc_code"].notna()]
    dest = DATA_DIR / "rucc.parquet"
    out.to_parquet(dest, index=False)
    print(f"  codes: {out['rucc_code'].value_counts().sort_index().to_dict()}")
    print(f"  wrote {dest.name}: {len(out)} counties")


# DOE Low-Income Energy Affordability Data (LEAD) Tool, 2022 update (ACS
# 2018-2022 based), one zip per state on OEDI: https://data.openei.org/submissions/6219
# (doi:10.25984/2504170). Each zip is about 58 MB; only the county file by
# Federal Poverty Level band is kept. Column meanings are from the
# submission's "Data Dictionary 2022.xlsx": UNITS = households, and
# X*UNITS = X summed over households, where HINCP is income and ELEP, GASP and
# FULP are electricity, gas and other fuel spending, all in $/year.
LEAD_URL = "https://data.openei.org/files/6219/{state}-2022-LEAD-data.zip"
LEAD_FILE = "{state} FPL Counties 2022.csv"
LEAD_COLUMNS = ["FIP", "FPL150", "UNITS", "HINCP*UNITS", "ELEP*UNITS", "GASP*UNITS", "FULP*UNITS"]
LEAD_BANDS = {"0-100%", "100-150%", "150-200%", "200-400%", "400%+"}
# Below 200% of the federal poverty level: the low-income cut DOE's
# Weatherization Assistance Program uses for household eligibility.
LEAD_LOW_INCOME_BANDS = {"0-100%", "100-150%", "150-200%"}


def _energy_burden(frame: pd.DataFrame) -> pd.Series:
    spend = frame["ELEP*UNITS"] + frame["GASP*UNITS"] + frame["FULP*UNITS"]
    return (spend / frame["HINCP*UNITS"]).where(frame["HINCP*UNITS"] > 0)


def prep_lead(counties: gpd.GeoDataFrame) -> None:
    """Energy burden (energy spending / income) per county, summed over
    households as the LEAD methodology does, for all households and for
    households below 200% of the poverty line. The US row sums the 50 states
    and DC."""
    import zipfile

    print("[lead]")
    states = sorted(set(counties["STUSPS"]))
    frames = []
    for state in states:
        csv_path = RAW_DIR / "lead" / LEAD_FILE.format(state=state)
        if not csv_path.exists():
            zip_path = download(
                LEAD_URL.format(state=state), RAW_DIR / "lead" / f"{state}-2022-LEAD-data.zip"
            )
            with zipfile.ZipFile(zip_path) as archive:
                name = LEAD_FILE.format(state=state)
                if name not in archive.namelist():
                    sys.exit(f"LEAD {state}: {name} not in zip; it has {archive.namelist()}")
                csv_path.write_bytes(archive.read(name))
            zip_path.unlink()  # 58 MB each; only the county file is needed
        df = pd.read_csv(csv_path, usecols=lambda c: c in LEAD_COLUMNS)
        require_columns(df, LEAD_COLUMNS, f"LEAD {state}")
        unknown = set(df["FPL150"].dropna().unique()) - LEAD_BANDS
        if unknown:
            sys.exit(f"LEAD {state}: unrecognized FPL bands {sorted(unknown)}")
        df["state"] = state
        frames.append(df)
    rows = pd.concat(frames, ignore_index=True)
    rows["county_fips"] = rows["FIP"].astype("int64").astype(str).str.zfill(5)
    sums = ["UNITS", "HINCP*UNITS", "ELEP*UNITS", "GASP*UNITS", "FULP*UNITS"]
    low = rows[rows["FPL150"].isin(LEAD_LOW_INCOME_BANDS)]

    by_county = rows.groupby("county_fips")[sums].sum()
    low_by_county = low.groupby("county_fips")[sums].sum()
    out = pd.DataFrame(
        {
            "households": by_county["UNITS"].round(),
            "burden_all": _energy_burden(by_county),
            "low_income_households": low_by_county["UNITS"].round(),
            "burden_low_income": _energy_burden(low_by_county),
        }
    ).reset_index()

    us_rows = rows[rows["state"] != "PR"]
    us_all = us_rows[sums].sum()
    us_low = us_rows[us_rows["FPL150"].isin(LEAD_LOW_INCOME_BANDS)][sums].sum()
    national = pd.DataFrame(
        {
            "county_fips": ["US"],
            "households": [round(us_all["UNITS"])],
            "burden_all": [_energy_burden(us_all.to_frame().T).iloc[0]],
            "low_income_households": [round(us_low["UNITS"])],
            "burden_low_income": [_energy_burden(us_low.to_frame().T).iloc[0]],
        }
    )
    out = pd.concat([out, national], ignore_index=True)
    dest = DATA_DIR / "energy_burden.parquet"
    out.to_parquet(dest, index=False)
    print(f"  US: {national.iloc[0].to_dict()}")
    print(f"  wrote {dest.name}: {len(out) - 1} counties")


# Treasury Office of Tax Analysis energy community data sets, listed at
# https://home.treasury.gov/policy-issues/tax-policy/data-transparency/all-treasury-generated-energy-communities-data-sets
# - EC_MSA_FFE_U2025.xlsx: IRS Notice 2026-39 Appendix 1, counties in MSAs and
#   non-MSAs that qualify through fossil fuel employment and 2025
#   unemployment. Applies from 2026-06-10 to the 2027 annual update.
# - EC_AllTracts_V5.xlsx: every 2020 census tract and whether it qualifies
#   through a coal mine closure or coal plant retirement (through Notice 2026-39).
# - EC_CC_V5.xlsx: the qualifying tracts with the reason (tract type).
EC_URL = "https://home.treasury.gov/system/files/131/{name}"
EC_MSA_NAME = "EC_MSA_FFE_U2025.xlsx"
EC_ALL_TRACTS_NAME = "EC_AllTracts_V5.xlsx"
EC_CC_NAME = "EC_CC_V5.xlsx"
EC_TRACT_COLUMN = "2020 Census Tract Number FIPS code"


def prep_energy_communities() -> None:
    print("[energy_communities]")
    msa = clean_columns(
        pd.read_excel(
            download(EC_URL.format(name=EC_MSA_NAME), RAW_DIR / EC_MSA_NAME), header=2, dtype=str
        )
    )
    print(f"  {EC_MSA_NAME} columns: {list(msa.columns)}")
    v1, v2 = (
        "Eligible under Vintage 1 MSA/non-MSA grouping",
        "Eligible under Vintage 2 MSA/non-MSA grouping",
    )
    require_columns(msa, ["State FIPS Code", "County FIPS Code", v1, v2], "EC MSA")
    msa = msa[msa["State FIPS Code"].notna() & msa["County FIPS Code"].notna()]
    flags = set(msa[v1].str.strip()) | set(msa[v2].str.strip())
    if not flags <= {"YES", "NO"}:
        sys.exit(f"EC MSA: unrecognized eligibility values {sorted(flags)}")
    msa_out = pd.DataFrame(
        {
            "county_fips": msa["State FIPS Code"].str.strip().str.zfill(2)
            + msa["County FIPS Code"].str.strip().str.zfill(3),
            "statistical_area_v1": msa[v1].str.strip().eq("YES"),
            "statistical_area_v2": msa[v2].str.strip().eq("YES"),
        }
    )

    tracts = clean_columns(
        pd.read_excel(
            download(EC_URL.format(name=EC_ALL_TRACTS_NAME), RAW_DIR / EC_ALL_TRACTS_NAME),
            header=1,
            dtype=str,
        )
    )
    print(f"  {EC_ALL_TRACTS_NAME} columns: {list(tracts.columns)}")
    qualifies = "Tract qualifies as a Coal Closure tract"
    require_columns(tracts, [EC_TRACT_COLUMN, qualifies], "EC all tracts")
    tracts = tracts[tracts[EC_TRACT_COLUMN].str.strip().str.fullmatch(r"\d{10,11}", na=False)]
    tracts["tract"] = tracts[EC_TRACT_COLUMN].str.strip().str.zfill(11)
    answers = set(tracts[qualifies].str.strip().unique())
    print(f"  coal closure answers: {sorted(answers)}")
    if not answers <= {"YES", "NO", "Yes", "No"}:
        sys.exit(f"EC all tracts: unrecognized answers {sorted(answers)}")
    tracts["cc"] = tracts[qualifies].str.strip().str.upper().eq("YES")
    tracts["county_fips"] = tracts["tract"].str[:5]

    cc = clean_columns(
        pd.read_excel(
            download(EC_URL.format(name=EC_CC_NAME), RAW_DIR / EC_CC_NAME), header=1, dtype=str
        )
    )
    require_columns(cc, [EC_TRACT_COLUMN, "Tract Type"], "EC coal closure tracts")
    cc = cc[cc[EC_TRACT_COLUMN].str.strip().str.fullmatch(r"\d{10,11}", na=False)]
    cc["county_fips"] = cc[EC_TRACT_COLUMN].str.strip().str.zfill(11).str[:5]
    kind = cc["Tract Type"].astype("string")
    cc["mine"] = kind.str.contains("Mine closure", na=False)
    cc["plant"] = kind.str.contains("Generating unit retirement", na=False)
    if int(tracts["cc"].sum()) != len(cc):
        flagged = int(tracts["cc"].sum())
        print(f"  note: {flagged} tracts flagged in all-tracts, {len(cc)} in CC list")

    by_county = (
        tracts.groupby("county_fips")
        .agg(total_tracts=("tract", "size"), coal_closure_tracts=("cc", "sum"))
        .join(
            cc.groupby("county_fips").agg(
                mine_closure_tracts=("mine", "sum"), plant_retirement_tracts=("plant", "sum")
            ),
            how="left",
        )
        .reset_index()
    )
    out = by_county.merge(msa_out, on="county_fips", how="outer")
    for column in (
        "total_tracts",
        "coal_closure_tracts",
        "mine_closure_tracts",
        "plant_retirement_tracts",
    ):
        out[column] = out[column].fillna(0).astype("int64")
    for column in ("statistical_area_v1", "statistical_area_v2"):
        out[column] = out[column].astype("boolean").fillna(False).astype(bool)
    out = out.sort_values("county_fips")
    dest = DATA_DIR / "energy_communities.parquet"
    out.to_parquet(dest, index=False)
    print(
        f"  wrote {dest.name}: {len(out)} counties, "
        f"{int((out['statistical_area_v1'] | out['statistical_area_v2']).sum())} statistical-area, "
        f"{int((out['coal_closure_tracts'] > 0).sum())} with coal closure tracts"
    )


# EIA-860 2025 annual electric generator report (final release, files dated
# 2026-09-09), https://www.eia.gov/electricity/data/eia860/
EIA860_URL = "https://www.eia.gov/electricity/data/eia860/xls/eia8602025.zip"
EIA860_PLANT_FILE = "2___Plant_Y2025.xlsx"
EIA860_GENERATOR_FILE = "3_1_Generator_Y2025.xlsx"
EIA860_PLANT_COLUMNS = [
    "Utility Name",
    "Plant Code",
    "Plant Name",
    "City",
    "State",
    "Latitude",
    "Longitude",
    "Sector Name",
    "Grid Voltage (kV)",
]
EIA860_GENERATOR_COLUMNS = ["Plant Code", "Technology", "Nameplate Capacity (MW)", "Status"]
# Fossil technologies as the file names them (every value seen on 2026-11-14).
FOSSIL_TECHNOLOGIES = {
    "Conventional Steam Coal",
    "Coal Integrated Gasification Combined Cycle",
    "Natural Gas Fired Combined Cycle",
    "Natural Gas Fired Combustion Turbine",
    "Natural Gas Internal Combustion Engine",
    "Natural Gas Steam Turbine",
    "Natural Gas with Compressed Air Storage",
    "Other Natural Gas",
    "Petroleum Liquids",
    "Petroleum Coke",
    "Other Gases",
}
# The IRS coal-closure category counts coal units retired since 2009-12-31;
# the same cut-off is used here for every fossil unit.
RETIRED_SINCE_YEAR = 2010


def prep_eia860(counties: gpd.GeoDataFrame) -> None:
    import zipfile

    print("[eia860]")
    src = download(EIA860_URL, RAW_DIR / "eia8602025.zip")
    with zipfile.ZipFile(src) as archive:
        plants = clean_columns(pd.read_excel(archive.open(EIA860_PLANT_FILE), header=1))
        operable = clean_columns(
            pd.read_excel(archive.open(EIA860_GENERATOR_FILE), sheet_name="Operable", header=1)
        )
        retired = clean_columns(
            pd.read_excel(
                archive.open(EIA860_GENERATOR_FILE), sheet_name="Retired and Canceled", header=1
            )
        )
    require_columns(plants, EIA860_PLANT_COLUMNS, "EIA-860 plants")
    require_columns(
        operable, [*EIA860_GENERATOR_COLUMNS, "Planned Retirement Year"], "EIA-860 operable"
    )
    require_columns(retired, [*EIA860_GENERATOR_COLUMNS, "Retirement Year"], "EIA-860 retired")
    known = set(operable["Technology"].dropna()) | set(retired["Technology"].dropna())
    print(f"  technologies: {sorted(known)}")

    operable["mw"] = pd.to_numeric(operable["Nameplate Capacity (MW)"], errors="coerce")
    operable["fossil"] = operable["Technology"].isin(FOSSIL_TECHNOLOGIES)
    operable["retire_year"] = pd.to_numeric(operable["Planned Retirement Year"], errors="coerce")
    retiring = operable["fossil"] & operable["retire_year"].notna()
    operable["retiring_mw"] = operable["mw"].where(retiring)
    operable["retiring_year"] = operable["retire_year"].where(operable["fossil"])
    operable["fossil_mw"] = operable["mw"].where(operable["fossil"])
    op = operable.groupby("Plant Code").agg(
        operable_mw=("mw", "sum"),
        fossil_mw=("fossil_mw", "sum"),
        planned_retirement_mw=("retiring_mw", "sum"),
        planned_retirement_year=("retiring_year", "min"),
    )
    op["primary_technology"] = (
        operable.groupby(["Plant Code", "Technology"])["mw"].sum().reset_index()
        .sort_values("mw", ascending=False)
        .drop_duplicates("Plant Code")
        .set_index("Plant Code")["Technology"]
    )

    retired = retired[retired["Status"].astype("string").str.strip().eq("RE")].copy()
    retired["mw"] = pd.to_numeric(retired["Nameplate Capacity (MW)"], errors="coerce")
    retired["year"] = pd.to_numeric(retired["Retirement Year"], errors="coerce")
    retired = retired[retired["Technology"].isin(FOSSIL_TECHNOLOGIES)]
    retired = retired[retired["year"] >= RETIRED_SINCE_YEAR]
    re = retired.groupby("Plant Code").agg(
        retired_fossil_mw=("mw", "sum"), last_retirement_year=("year", "max")
    )
    re["retired_technology"] = (
        retired.groupby(["Plant Code", "Technology"])["mw"].sum().reset_index()
        .sort_values("mw", ascending=False)
        .drop_duplicates("Plant Code")
        .set_index("Plant Code")["Technology"]
    )

    gens = op.join(re, how="outer")
    out = plants[EIA860_PLANT_COLUMNS].merge(
        gens, left_on="Plant Code", right_index=True, how="inner"
    )
    out = pd.DataFrame(
        {
            "plant_id": out["Plant Code"].astype("int64"),
            "name": out["Plant Name"].astype("string").str.strip(),
            "utility_name": out["Utility Name"].astype("string").str.strip(),
            "city": out["City"].astype("string").str.strip(),
            "state": out["State"].astype("string").str.strip(),
            "sector": out["Sector Name"].astype("string").str.strip(),
            "lat": pd.to_numeric(out["Latitude"], errors="coerce"),
            "lon": pd.to_numeric(out["Longitude"], errors="coerce"),
            "grid_voltage_kv": pd.to_numeric(out["Grid Voltage (kV)"], errors="coerce"),
            "primary_technology": out["primary_technology"]
            .fillna(out["retired_technology"])
            .astype("string"),
            "operable_mw": out["operable_mw"].fillna(0).round(1),
            "fossil_mw": out["fossil_mw"].fillna(0).round(1),
            "planned_retirement_mw": out["planned_retirement_mw"].fillna(0).round(1),
            "planned_retirement_year": out["planned_retirement_year"].astype("Int64"),
            "retired_fossil_mw": out["retired_fossil_mw"].fillna(0).round(1),
            "last_retirement_year": out["last_retirement_year"].astype("Int64"),
        }
    )
    # A plant whose fossil units are retiring or have retired since 2010: its
    # grid connection and land are a candidate for clean power or storage.
    out["reuse_candidate"] = (out["planned_retirement_mw"] > 0) | (out["retired_fossil_mw"] > 0)
    out = out[out["lat"].notna() & out["lon"].notna()]
    out["county_fips"] = assign_county_fips(out, counties)
    dropped = int(out["county_fips"].isna().sum())
    out = out[out["county_fips"].notna()].reset_index(drop=True)
    dest = DATA_DIR / "power_plants.parquet"
    out.to_parquet(dest, index=False)
    print(
        f"  wrote {dest.name}: {len(out)} plants, {int(out['reuse_candidate'].sum())} with "
        f"retiring or retired fossil units ({dropped} dropped: outside every county)"
    )


# EIA-861 2025 annual electric power industry report (files dated 2026-10-07),
# https://www.eia.gov/electricity/data/eia861/
EIA861_URL = "https://www.eia.gov/electricity/data/eia861/zip/f8612025.zip"


def prep_eia861(counties: gpd.GeoDataFrame) -> None:
    """Utilities serving each county (Service_Territory), with their ownership
    (Utility_Data) and in-state residential price (Sales_Ult_Cust, bundled
    service only: delivery-only and energy-only rows are a part of a bill)."""
    import zipfile

    print("[eia861]")
    src = download(EIA861_URL, RAW_DIR / "f8612025.zip")
    with zipfile.ZipFile(src) as archive:
        territory = pd.read_excel(
            archive.open("Service_Territory_2025.xlsx"), sheet_name="Counties_States"
        )
        sales_raw = pd.read_excel(
            archive.open("Sales_Ult_Cust_2025.xlsx"), sheet_name="States", header=None
        )
        utilities = pd.read_excel(
            archive.open("Utility_Data_2025.xlsx"), sheet_name="States", header=1
        )
    territory, utilities = clean_columns(territory), clean_columns(utilities)
    require_columns(territory, ["Utility Number", "Utility Name", "State", "County"], "EIA-861 ST")
    require_columns(utilities, ["Utility Number", "State", "Ownership Type"], "EIA-861 utility")

    # Sales: three header rows. Row 0 names the customer class over its three
    # columns, row 2 the field. Columns 9-11 must be residential revenue
    # (thousand dollars), sales (MWh) and customers.
    head = sales_raw.iloc[:3].astype("string")
    if (
        head.iat[0, 9] != "RESIDENTIAL"
        or [head.iat[2, i] for i in (1, 4, 6, 9, 10, 11)]
        != ["Utility Number", "Service Type", "State", "Thousand Dollars", "Megawatthours", "Count"]
    ):
        sys.exit(f"EIA-861 sales: unexpected header rows:\n{head.iloc[:, :12].to_string()}")
    sales = sales_raw.iloc[3:, [1, 4, 6, 9, 10, 11]].copy()
    sales.columns = ["utility_id", "service_type", "state", "revenue_k", "mwh", "customers"]
    print(f"  service types: {sorted(sales['service_type'].dropna().unique())}")
    for column in ("utility_id", "revenue_k", "mwh", "customers"):
        sales[column] = pd.to_numeric(sales[column], errors="coerce")
    sales = sales[sales["utility_id"].notna()]
    customers = sales.groupby(["utility_id", "state"])["customers"].sum()
    bundled = sales[sales["service_type"].eq("Bundled")].groupby(["utility_id", "state"])[
        ["revenue_k", "mwh"]
    ].sum()
    # thousand $ / MWh * 100 = cents per kWh
    price = (bundled["revenue_k"] / bundled["mwh"] * 100).where(bundled["mwh"] > 0)

    # Keyed on the full legal name with only county-type suffixes removed, so
    # "Baltimore" (the county) and "Baltimore City" stay apart.
    lookup = {
        (state, plain_key(name)): geoid
        for geoid, name, state in zip(
            counties["GEOID"], counties["NAMELSAD"], counties["STUSPS"], strict=True
        )
    }
    territory["county_fips"] = [
        lookup.get((str(state).strip(), plain_key(name)))
        for state, name in zip(territory["State"], territory["County"], strict=True)
    ]
    unmatched = territory[territory["county_fips"].isna()]
    if len(unmatched):
        # Connecticut reports its old counties, replaced in 2022 by planning
        # regions; the rest are former Alaska and Virginia areas.
        print(
            f"  {len(unmatched)} utility-county rows not matched to a 2025 county: "
            f"{sorted(set(zip(unmatched['State'], unmatched['County'], strict=True)))}"
        )
    territory = territory[territory["county_fips"].notna()].copy()
    territory["utility_id"] = pd.to_numeric(territory["Utility Number"], errors="coerce")
    # Utility_Data lists a utility once, under one state, even when it serves
    # several (Appalachian Power: VA, not WV), so ownership is looked up by id.
    ownership_by_id = (
        utilities.assign(utility_id=pd.to_numeric(utilities["Utility Number"], errors="coerce"))
        .drop_duplicates("utility_id")  # "Withheld" (88888) repeats
        .set_index("utility_id")["Ownership Type"]
    )
    keys = list(zip(territory["utility_id"], territory["State"], strict=True))
    out = pd.DataFrame(
        {
            "county_fips": territory["county_fips"],
            "utility_id": territory["utility_id"].astype("Int64"),
            "utility_name": territory["Utility Name"].astype("string").str.strip(),
            "state": territory["State"].astype("string").str.strip(),
            "ownership": [ownership_by_id.get(k[0]) for k in keys],
            "residential_customers": [customers.get(k) for k in keys],
            "residential_price_cents_kwh": [price.get(k) for k in keys],
        }
    )
    out["ownership"] = out["ownership"].astype("string")
    out["residential_customers"] = pd.to_numeric(out["residential_customers"]).astype("Int64")
    out["residential_price_cents_kwh"] = pd.to_numeric(out["residential_price_cents_kwh"]).round(2)
    out = out.drop_duplicates(["county_fips", "utility_id"]).sort_values(
        ["county_fips", "residential_customers"], ascending=[True, False]
    )
    dest = DATA_DIR / "utilities.parquet"
    out.to_parquet(dest, index=False)
    print(f"  ownership: {out['ownership'].value_counts().to_dict()}")
    print(f"  wrote {dest.name}: {len(out)} utility-county rows in {out['county_fips'].nunique()}")


# CDC PLACES 2025 release, county data in the GIS-friendly layout (one row per
# county), dataset i46a-9kgh on data.cdc.gov. Values are crude prevalence among
# adults, in percent; 2023 BRFSS for these measures. The social-needs measures
# (shut-off, food, housing, transportation) come from an optional BRFSS module,
# so about 2,300 counties have them.
PLACES_URL = "https://data.cdc.gov/resource/i46a-9kgh.csv"
PLACES_MEASURES = {
    "casthma_crudeprev": "asthma",
    "copd_crudeprev": "copd",
    "shututility_crudeprev": "utility_shutoff_threat",
    "foodinsecu_crudeprev": "food_insecurity",
    "housinsecu_crudeprev": "housing_insecurity",
    "lacktrpt_crudeprev": "lack_transportation",
}


def prep_places() -> None:
    """The US row is our own figure: the average of all counties weighted by
    adult population. PLACES publishes no national row."""
    print("[places]")
    dest_raw = RAW_DIR / "places_county_2025.csv"
    if not dest_raw.exists():
        columns = ["countyfips", "stateabbr", "totalpop18plus", *PLACES_MEASURES]
        resp = httpx.get(
            PLACES_URL,
            params={"$select": ",".join(columns), "$limit": 10000},
            headers={"User-Agent": USER_AGENT},
            timeout=120,
        )
        resp.raise_for_status()
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        dest_raw.write_bytes(resp.content)
    df = pd.read_csv(dest_raw, dtype={"countyfips": str})
    print(f"  columns: {list(df.columns)}")
    require_columns(df, ["countyfips", "totalpop18plus", *PLACES_MEASURES], "PLACES")
    out = pd.DataFrame({"county_fips": df["countyfips"].str.zfill(5)})
    for column, name in PLACES_MEASURES.items():
        out[name] = pd.to_numeric(df[column], errors="coerce") / 100
    adults = pd.to_numeric(df["totalpop18plus"], errors="coerce")
    us = {"county_fips": "US"}
    for name in PLACES_MEASURES.values():
        has = out[name].notna() & adults.notna()
        us[name] = float((out.loc[has, name] * adults[has]).sum() / adults[has].sum())
    out = pd.concat([out, pd.DataFrame([us])], ignore_index=True).drop_duplicates("county_fips")
    dest = DATA_DIR / "places_counties.parquet"
    out.to_parquet(dest, index=False)
    shown = {k: round(v, 4) for k, v in us.items() if k != "county_fips"}
    print(f"  US (adult-weighted): {shown}")
    print(f"  wrote {dest.name}: {len(out) - 1} counties")


# EPA air quality design values, 2023-2025 (final, June 2026), linked from
# https://www.epa.gov/air-trends/air-quality-design-values. County tables give
# the highest valid design value among the county's monitors; most counties
# have no monitor. Standards are in each table's notes and in config.py.
PM25_DV_URL = (
    "https://www.epa.gov/system/files/documents/2026-06/pm25_designvalues_2023_2025_final_06_12_26.xlsx"
)
O3_DV_URL = (
    "https://www.epa.gov/system/files/documents/2026-06/o3_designvalues_2023_2025_final_06_08_26.xlsx"
)
DV_TABLES = [
    (PM25_DV_URL, "pm25_dv_2023_2025.xlsx", "Table4a. County Status Annual", "pm25_annual_ugm3"),
    (PM25_DV_URL, "pm25_dv_2023_2025.xlsx", "Table4b. County Status 24-hr", "pm25_24h_ugm3"),
    (O3_DV_URL, "o3_dv_2023_2025.xlsx", "Table4. County Status", "ozone_ppm"),
]


def prep_air() -> None:
    print("[air]")
    merged: pd.DataFrame | None = None
    for url, filename, sheet, column in DV_TABLES:
        src = download(url, RAW_DIR / filename)
        raw = pd.read_excel(src, sheet_name=sheet, header=None)
        notes = [str(v) for v in raw.iloc[:, 0] if str(v).startswith("1. The level")]
        print(f"  {sheet}: {notes[0] if notes else 'no level note found'}")
        df = pd.read_excel(src, sheet_name=sheet, header=3, dtype=str)
        require_columns(df, ["State FIPS", "County FIPS"], f"design values {sheet}")
        value = [c for c in df.columns if "Design Value" in str(c)]
        if len(value) != 1:
            sys.exit(f"design values {sheet}: expected one value column in {list(df.columns)}")
        df = df[df["State FIPS"].str.fullmatch(r"\d{1,2}", na=False)]
        table = pd.DataFrame(
            {
                "county_fips": df["State FIPS"].str.zfill(2) + df["County FIPS"].str.zfill(3),
                column: pd.to_numeric(df[value[0]], errors="coerce"),
            }
        ).dropna()
        merged = table if merged is None else merged.merge(table, on="county_fips", how="outer")
    assert merged is not None
    dest = DATA_DIR / "air_quality.parquet"
    merged.sort_values("county_fips").to_parquet(dest, index=False)
    print(f"  wrote {dest.name}: {len(merged)} counties with a monitored design value")


# USDA Forest Service, Wildfire Risk to Communities v2, Research Data Archive
# RDS-2024-0030 supplements (county summary tables for 2024, 2025, 2026; field
# descriptions in WRC_V2_SummaryTableFieldDescriptions.csv). About 62 MB.
WILDFIRE_URL = (
    "https://www.fs.usda.gov/rds/archive/products/RDS-2024-0030/RDS-2024-0030_Supplements.zip"
)
WILDFIRE_MEMBER = "Supplements/Summaries/CSV/2026/WRC_V2_County_Summary_2026.csv"
WILDFIRE_FIELDS = {
    "GEOID": "county_fips",
    "RISK_NATIONAL_RANK": "risk_to_homes_national_rank",
    "RISK_STATE_RANK": "risk_to_homes_state_rank",
    "HU_FRACTION_DE": "housing_directly_exposed",
    "HU_FRACTION_IE": "housing_indirectly_exposed",
    "TOTAL_HOUSING_UNITS": "housing_units",
}


def prep_wildfire() -> None:
    import zipfile

    print("[wildfire]")
    src = download(WILDFIRE_URL, RAW_DIR / "RDS-2024-0030_Supplements.zip")
    with zipfile.ZipFile(src) as archive:
        df = pd.read_csv(archive.open(WILDFIRE_MEMBER), dtype={"GEOID": str}, encoding="latin-1")
    print(f"  columns: {list(df.columns)}")
    require_columns(df, list(WILDFIRE_FIELDS), "Wildfire Risk to Communities")
    out = df[list(WILDFIRE_FIELDS)].rename(columns=WILDFIRE_FIELDS)
    out["county_fips"] = out["county_fips"].str.zfill(5)
    for column in out.columns[1:]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    dest = DATA_DIR / "wildfire_counties.parquet"
    out.drop_duplicates("county_fips").sort_values("county_fips").to_parquet(dest, index=False)
    print(f"  wrote {dest.name}: {len(out)} counties")


STEPS = (
    "counties",
    "repowering",
    "lmop",
    "nri",
    "energy",
    "dams",
    "farms",
    "svi",
    "rucc",
    "lead",
    "energy_communities",
    "eia860",
    "eia861",
    "places",
    "air",
    "wildfire",
)


def main(argv: list[str]) -> None:
    steps = argv or list(STEPS)
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        sys.exit(f"Unknown step(s) {unknown}. Choose from {STEPS}.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    counties = prep_counties() if "counties" in steps else None
    if {"repowering", "lmop", "dams", "farms", "lead", "eia860", "eia861"} & set(steps):
        counties = counties if counties is not None else load_counties()
    if "repowering" in steps:
        prep_repowering(counties)
    if "lmop" in steps:
        prep_lmop(counties)
    if "nri" in steps:
        prep_nri()
    if "energy" in steps:
        prep_energy()
    if "dams" in steps:
        prep_dams(counties)
    if "farms" in steps:
        prep_farms(counties)
    if "svi" in steps:
        prep_svi()
    if "rucc" in steps:
        prep_rucc()
    if "lead" in steps:
        prep_lead(counties)
    if "energy_communities" in steps:
        prep_energy_communities()
    if "eia860" in steps:
        prep_eia860(counties)
    if "eia861" in steps:
        prep_eia861(counties)
    if "places" in steps:
        prep_places()
    if "air" in steps:
        prep_air()
    if "wildfire" in steps:
        prep_wildfire()
    print("done")


if __name__ == "__main__":
    main(sys.argv[1:])
