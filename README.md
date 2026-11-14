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

`GEMINI_API_KEY` is optional: without it the brief comes from a fixed template.

```bash
# backend (Python 3.11+; developed on 3.14)
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # then fill in the keys
python scripts/prep_data.py          # once: ~90 MB download, about 90 seconds
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

Working and tested (85 backend tests; frontend builds in strict mode):

- `prep_data.py` builds all four national files from the real sources.
- `POST /api/analyze` for a county or a box, with every source under a 15 s
  timeout, cached on disk, and reported in `source_status` on failure.
- Needs, matching, estimates, funding and the template narrative.
- Results panel, county search, loading and error states, fixture mode.

Written but not yet run against the real service:

- **The map** (`MapView.tsx`, `boxSelect.ts`): no Mapbox token was available.
  It type-checks; clicking, hover, markers and box select are untested.
- **Census**: no key was available. Parsing and formulas are unit-tested
  against the documented response shape, not a live call.
- **Gemini**: no key. The call, number validation, caching and fallback are
  tested with a fake model.

`backend/data/fixtures/54055.json` (Mercer County, WV) is a real report, but a
partial one: it was saved with no Census key and while Overpass was at
capacity, so it has two needs and one opportunity. Regenerate it once the keys
are in place.

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

Still unverified: the 60% usable-land assumption, FEMA and state funding
programs, and every stretch data source.

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
- **Contract additions** in `models.py`: `Need.detail`, `asset_counts`,
  `missing_asset_types`, extra `context` fields, and `agency` and `key_terms`
  on funding matches.
- **Funding table.** Six entries, all from section 11. Public EV charging and
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
