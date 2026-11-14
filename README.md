# County Sustainability Planner

Pick your county, see your biggest sustainability gaps, the local sites that
could fix them, and how to pay for it.


## Setup

You need three keys before anything useful runs:

| Key | Where | Needed for |
|---|---|---|
| `CENSUS_API_KEY` | `backend/.env` | Eight of the eleven needs. The Census API rejects keyless calls. |
| `NLR_API_KEY` | `backend/.env` | Solar yield and EV stations. |
| `VITE_MAPBOX_TOKEN` | `frontend/.env` | The map. Search and reports work without it. |

`GEMINI_API_KEY` writes the opportunities and the brief. Without it the report
still loads, with opportunities from the fixed rules and a template brief.

```bash
# backend (Python 3.11+; developed on 3.14)
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # then fill in the keys
python scripts/prep_data.py          # once: ~420 MB download, about two minutes
uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && npm install
cp .env.example .env                 # then add the Mapbox token
npm run dev                          # http://localhost:5173
```

Checks: `pytest -q` and `ruff check .` in `backend/`; `npm run build` in `frontend/`.

Demo without a network: `python scripts/warm_cache.py <fips> ...` saves reports
to `backend/data/fixtures/`, and `http://localhost:5173/?fixture=1` serves them.

## Status

Working and tested (131 backend tests; frontend builds in strict mode):

- `prep_data.py` builds all seven national files from the real sources.
- `POST /api/analyze` for a county or a box, with every source under a 15 s
  timeout, cached on disk, and reported in `source_status` on failure.
- Needs, matching, estimates, funding and the template narrative.
- Results panel, county search, loading and error states, fixture mode.

Run against the real services on 2026-11-14: Census ACS (three counties),
PVWatts, the station API, and the map (county highlight, markers and legend
seen in a headless browser).

Not yet run against the real service:

- **Map interaction**: clicking a county, hover, popups and box select have
  not been driven. Selection so far has gone through the search box.
- **Overpass**: every call so far has timed out or been refused as busy, so no
  report has had real public buildings in it.

Gemini was run for Mercer County, WV and Glacier County, MT on 2026-11-14 with
`gemini-3.5-flash-lite`; the rest of its handling is tested with a fake model.

`backend/data/fixtures/54055.json` (Mercer County, WV) is a real report saved
on 2026-11-14 with Gemini-written opportunities, on a machine without the
public-building and Earth Engine keys: it has no schools or libraries and no
heat or tree-canopy figures. Regenerate it with `warm_cache.py 54055` where
those keys are set.

## Stretch sources

Added on 2026-11-14. Each file was opened before its columns were mapped.

| Source | File and year | What it adds |
|---|---|---|
| EIA electric sales data | Table 5A, 2024 | Need `high_electricity_cost`: the state's average residential monthly bill against the US average. |
| EPA eGRID | eGRID2023 rev 2 | Need `carbon_intensive_grid`: CO2 per MWh generated in the state against the US rate. |
| ORNL HydroSource | Non-powered dam assessment, 2025 | Asset `non_powered_dam` (2,616 dams, lower 48) and solution `small_hydro`. Capacity and energy are ORNL's model results. |
| USDA Census of Agriculture | NASS bulk file, 2022 | Farmland acres, and counts of dairies with 500 or more cows and hog farms with 1,000 or more hogs. |
| EPA AgSTAR | Digester database | Operating manure digesters per county. With the census counts it drives need `unmanaged_farm_waste` and solution `farm_digester`. |
| EPA RE-Powering (already loaded) | 2022 dataset | Solution `wind_on_reclaimed_land` from EPA's per-site wind estimate, and distance to a transmission line in the map popup. |

Things to know about them:

- **Bills and grid carbon are state averages.** Neither agency publishes a
  county figure. The panel labels the bar "State average". The eGRID rate is
  for power generated in the state, not power used there.
- **Farm digesters have no map point.** The census gives counts, not farm
  locations, so that opportunity is county-wide and `asset_ids` is empty.
- **Hog threshold.** EPA's screening size is 500 cows or 2,000 hogs. The census
  has no 2,000-hog class by county, so a county with only 1,000-hog farms is
  `elevated`, not `high`.
- **Dams.** Many of the largest are federal locks and dams. The card shows the
  owner type; the tool does not check whether the owner would allow a project.
- **Wind.** EPA's attribute sheet says "80 MW per acre"; the data is 80 acres
  per MW. There is no energy figure, because we have no wind resource source.
- **AgSTAR county names.** Nine misspelled names are corrected in
  `prep_data.py`. One Texas project names a county that does not exist and is
  left out.
- **Connecticut has no farm data.** The 2022 census used counties the state
  has since replaced.
- **Funding.** Hydropower has its own tax-credit entry with no deadline,
  because the plan says only that its deadlines are later. REAP now also
  lists `farm_digester`; its status is still `uncertain`.

Not added:

- **Earth Engine** (forest residue, open land, paved lots, tree canopy,
  surface heat): it needs a Google Cloud project and credentials.
- **Streams, impaired waters, crop and forest biomass**: no source was found
  that gives a county figure without a large download or an invented formula.
- **OSM transmission lines**: Overpass is not answering; the RE-Powering
  distance column is used instead.

## What verification found

Items the original project plan marked VERIFY, checked on 2026-11-13:

| Item | Finding |
|---|---|
| ACS variable codes | All twelve formulas match the 2024 `variables.json` labels. |
| Census API access | Keyless calls now redirect to `missing_key.html`. A key is required. |
| County boundary file | `cb_2025_us_county_5m.zip` is the latest. AS, GU, MP and VI are dropped (no ACS data). |
| RE-Powering columns | No FIPS column, so sites are assigned to counties by location. The file does have per-site wind screening columns. |
| Solar acres per MW | EPA's own figure in that dataset is 6.9 acres per MW, capped at 600 MW. `estimates.py` uses 6.9, not 6. |
| Household consumption | EIA's FAQ gives 10,791 kWh for 2022. `estimates.py` uses that, not 10,500. |
| LMOP columns | "Current Project Status" has a `Candidate` value with a written definition; that drives `landfill_gas_uncaptured`. About 300 landfills have no coordinates. |
| NRI download | `hazards.fema.gov/nri` now redirects elsewhere and the zip on fema.gov blocks scripts. `prep_data.py` reads FEMA's ArcGIS feature service instead (same v1.20 data). v1.20 has "inland flooding", not "riverine". |
| NLR station API | `fuel_type`, `state`, `status`, `access` confirmed. `DEMO_KEY` allows about 10 calls an hour. |
| Overpass | Needs a `User-Agent` header (406 without). The public server was at capacity during testing. |
| `google-genai` config | `response_mime_type` and `response_schema` exist in SDK 3.3.0. |

Still unverified: the 60% usable-land assumption, and FEMA and state funding
programs. EIA's Table 5A gives 863 kWh a month for 2024 (10,359 a year);
`estimates.py` still uses the 2022 FAQ figure.

## Where the code departs from the original plan

- **Site types.** RE-Powering sites are typed `brownfield` only when they come
  from a brownfields program. Superfund, RCRA and state cleanup sites are
  `contaminated_site`, so the EPA Brownfields grant is not offered for them.
  Landfills are `landfill`, not "closed landfill": the file does not say.
- **Opportunity ranking.** Each solution's best site is selected before any
  second site, then the list is ordered by score. A strict top six by score
  dropped whole solutions in favor of second charger locations.
- **EV benchmark.** Computed per request from two cached calls (national
  station count and ACS national population) instead of a hard-coded constant.
  Set `EV_BENCHMARK_PER_10K` to pin it.
- **Ranking by priority, not severity.** Each need carries a `priority`:
  its severity times a weight (`need_priority_weights` by theme and
  `need_priority_overrides` by need, both in `config.py`). Needs are ordered by
  it. An opportunity's score is halfway between the highest and the mean
  priority of the needs it addresses (the plan used the mean severity alone,
  which marked a solution down for also answering lesser needs).
  Three tiers: landfill gas, farm waste, the carbon-intensive grid and natural
  hazard risk count in full; household energy, heat and tree cover, and equity
  count 0.7; transportation counts 0.5. The weights are our own choice for the
  sustainability track, made because the EV charging gap sits at severity 100
  in most counties and led almost every report. There is no water-quality
  source: water enters only through flood and drought in the hazard rating.
- **Gemini writes the opportunities.** This replaces the rule matcher as the
  main path and departs from rule 2 of the plan. A report now has three parts:
  1. **Needs** (`needs.py`), unchanged: problems flagged from the data.
  2. **Assets and factors** (`factors.py`): what the county has to work with.
     Mapped sites as before, plus county-level facts on climate, energy, land,
     buildings, workforce, economy, infrastructure and agriculture, each with
     the US figure beside it where one exists. Shown in the Assets tab.
  3. **Opportunities** (`opportunities.py`): one Gemini call per county is sent
     a digest of parts 1 and 2 and returns up to `MAX_OPPORTUNITIES` projects
     plus the county summary. Each names the needs it answers, the factors and
     sites it draws on, and first steps.

  Code still supplies every number. An opportunity that names a need, factor,
  site or number missing from the digest is dropped. The score comes from the
  priority of the needs addressed. Size estimates and funding leads are added
  only when Gemini labels the project as a type from `solutions.json` and
  names a site of a kind that type is built at; its other ideas show neither.
  With no key, an error, a timeout or nothing valid, the rule matches from
  `matching.py` are served instead and marked `generated_by: "rules"`.
- **Workforce and housing figures** come from extra ACS variables, checked
  against the 2024 variable list on 2026-11-14: `C24050` (industry of
  employment), `B15003` (education) and `B25002` (vacancy).
- **Contract additions** in `models.py`: `Need.detail`, `Need.priority`,
  `factors`, `asset_counts`, `missing_asset_types`, extra `context` and
  `resources` fields, and `agency` and `key_terms` on funding matches. An
  opportunity has `asset_ids` and `factor_ids` (either can be empty),
  `summary`, `rationale`, `first_steps` and `generated_by`; `solution_id` is
  null for a project that is not in the catalogue.
- **Funding table.** Seven entries, all from section 11. Public EV charging and
  landfill gas have no program yet, and no state programs are entered.
- `sources/_parquet.py` and `frontend/src/format.ts` are small shared helpers
  not in the original layout.

## Known limits

- Overpass is the weakest dependency: three of the five solutions need its
  buildings. Warm the cache for demo counties ahead of time.
- The station API returns a whole state per call. The largest states may
  exceed the 15 s timeout on a cold cache.
- Counties with thousands of sites show the 150 largest on the map; the counts
  in the panel are exact.
- The RE-Powering file lists some sites twice under the same name, so the same
  opportunity can appear twice (seen in Lincoln County, MO).
- Eight solutions now compete for six places, so a county with many flagged
  needs can lose a lower-scoring solution from the list.
