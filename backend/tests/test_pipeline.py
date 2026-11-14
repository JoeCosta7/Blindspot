"""The whole pipeline with every source faked. No network, no prepared files."""

import asyncio

import pytest

from app import pipeline
from app.sources import SourceError, afdc, census, lmop, nri, overpass, pvwatts, repowering
from tests.conftest import make_county, make_metrics

COUNTY = make_county("99001", 0, 0, 1, 1)


def returning(value, cached=False):
    async def fake(*args, **kwargs):
        return value, cached

    return fake


def raising(exc):
    async def fake(*args, **kwargs):
        raise exc

    return fake


@pytest.fixture
def sources(monkeypatch):
    """Healthy fakes for every source. Tests override individual ones."""
    county = make_metrics(
        population=20_000,
        median_household_income=55_000.0,  # ratio 1.45 -> high
        pre1980_housing_share=0.70,  # ratio 1.4 -> high
    )
    monkeypatch.setattr(census, "fetch_county", returning(county))
    monkeypatch.setattr(census, "fetch_national", returning(make_metrics(population=330_000_000)))
    monkeypatch.setattr(pvwatts, "fetch_yield", returning(1200.0))
    monkeypatch.setattr(afdc, "fetch_national_count", returning(82_500))
    monkeypatch.setattr(
        afdc,
        "fetch_state_stations",
        returning(
            [
                {
                    "id": 1,
                    "name": "Inside",
                    "city": None,
                    "lat": 0.5,
                    "lon": 0.5,
                    "level2_ports": 2,
                    "dc_fast_ports": None,
                },
                {
                    "id": 2,
                    "name": "Other county",
                    "city": None,
                    "lat": 5.0,
                    "lon": 5.0,
                    "level2_ports": 2,
                    "dc_fast_ports": None,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        overpass,
        "fetch_public_buildings",
        returning(
            [
                {
                    "osm_id": "node/1",
                    "type": "library",
                    "name": "Main Library",
                    "lat": 0.5,
                    "lon": 0.5,
                    "city": None,
                },
                {
                    "osm_id": "way/2",
                    "type": "school",
                    "name": "Central School",
                    "lat": 0.6,
                    "lon": 0.6,
                    "city": None,
                },
                {
                    "osm_id": "way/3",
                    "type": "school",
                    "name": "Bbox Only School",
                    "lat": 1.5,
                    "lon": 0.5,
                    "city": None,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        repowering,
        "sites_for_county",
        lambda data_dir, fips: [
            {
                "site_id": 10,
                "name": "OLD MILL SITE",
                "site_type": "brownfield",
                "program": "BROWNFIELDS",
                "city": "MILLTOWN",
                "lat": 0.4,
                "lon": 0.4,
                "acres": 69.0,
                "pv_scale": "utility",
                "wind_scale": None,
                "epa_pv_mw": 10.0,
                "transmission_miles": 1.23,
                "profile_url": None,
            },
            {
                "site_id": 11,
                "name": "Depot",
                "site_type": "contaminated_site",
                "program": "RCRA",
                "city": None,
                "lat": 0.3,
                "lon": 0.3,
                "acres": None,
                "pv_scale": "unknown_acreage",
                "wind_scale": None,
                "epa_pv_mw": None,
                "transmission_miles": None,
                "profile_url": None,
            },
        ],
    )
    monkeypatch.setattr(
        lmop,
        "landfills_for_county",
        lambda data_dir, fips: [
            {
                "landfill_id": 7,
                "name": "County Landfill",
                "lat": 0.2,
                "lon": 0.2,
                "status": "Open",
                "project_status": "Candidate",
                "is_candidate": True,
                "has_operational_project": False,
                "operational_mw": None,
                "waste_in_place_tons": 2_000_000.0,
                "lfg_collected_mmscfd": 0.5,
                "lfg_collected_year": 2022.0,
                "lfg_generated_mmscfd": None,
                "ownership_type": "Public",
            },
            {
                "landfill_id": 8,
                "name": "No Coordinates LF",
                "lat": None,
                "lon": None,
                "status": "Closed",
                "project_status": "Low Potential",
                "is_candidate": False,
                "has_operational_project": False,
            },
        ],
    )
    monkeypatch.setattr(
        nri,
        "ratings_for_county",
        lambda data_dir, fips: {
            "nri_version": "December 2025",
            "wildfire_rating": "Relatively High",
        },
    )


def analyze(settings):
    return asyncio.run(pipeline.analyze_county(COUNTY, client=None, settings=settings))


def test_full_report(sources, settings):
    report = analyze(settings)

    assert (report.fips, report.population) == ("99001", 20_000)
    assert report.context.median_household_income == 55_000.0
    assert report.resources.solar_kwh_per_kw_year == 1200

    need_ids = [n.id for n in report.needs]
    assert set(need_ids) == {
        "low_income",
        "old_housing",
        "ev_charging_gap",  # 0.5 per 10k against a benchmark of 2.5
        "landfill_gas_uncaptured",
        "hazard_risk",
    }
    severities = [n.severity for n in report.needs]
    assert severities == sorted(severities, reverse=True)

    # Points outside the county polygon are dropped.
    names = {a.name for a in report.assets}
    assert "Other county" not in names and "Bbox Only School" not in names
    assert "Old Mill Site" in names  # upper-case EPA names are tidied
    assert report.asset_counts == {
        "brownfield": 1,
        "contaminated_site": 1,
        "lmop_landfill": 1,
        "library": 1,
        "school": 1,
        "ev_station": 1,
    }
    assert set(report.missing_asset_types) == {"landfill", "mine_land", "college"}

    by_id = {o.id: o for o in report.opportunities}
    assert len(report.opportunities) <= settings.max_opportunities
    solar = by_id["community_solar:repowering:10"]
    assert solar.need_ids == ["low_income"]
    assert {e.label: e.value for e in solar.estimates} == {
        "Capacity": 6.0,
        "Annual energy": 7200.0,
        "Household equivalent": 670.0,
    }
    assert all(e.is_estimate and e.assumptions for o in report.opportunities for e in o.estimates)
    assert solar.funding[0].program_id == "epa_brownfields_fy27"

    gas = by_id["landfill_gas_energy:lmop:7"]
    assert gas.funding == []
    assert gas.estimates[0].label == "Landfill gas collected"

    asset_ids = {a.id for a in report.assets}
    assert all(o.asset_id in asset_ids for o in report.opportunities)

    assert report.narrative.generated_by == "template"
    status = {s.source: s for s in report.source_status}
    assert all(s.ok for name, s in status.items() if name != "gemini")
    assert "no coordinates" in status["lmop"].message


def test_failed_sources_do_not_fail_the_request(sources, settings, monkeypatch):
    monkeypatch.setattr(census, "fetch_county", raising(SourceError("CENSUS_API_KEY is not set")))
    monkeypatch.setattr(overpass, "fetch_public_buildings", raising(RuntimeError("boom")))

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(pvwatts, "fetch_yield", slow)
    quick = settings.model_copy(update={"source_timeout_s": 0.05})

    report = analyze(quick)
    status = {s.source: s for s in report.source_status}

    assert status["census"].ok is False and "CENSUS_API_KEY" in status["census"].message
    assert status["overpass"].message == "Unexpected error: RuntimeError"
    assert "Timed out" in status["pvwatts"].message
    assert status["repowering"].ok and status["lmop"].ok and status["nri"].ok

    # Needs that do not depend on the failed sources are still found.
    assert {n.id for n in report.needs} == {"landfill_gas_uncaptured", "hazard_risk"}
    assert report.population is None
    assert report.resources.solar_kwh_per_kw_year is None
    # A failed source is "unknown", not "not found".
    assert "school" not in report.missing_asset_types
    assert [o.solution_id for o in report.opportunities] == ["landfill_gas_energy"]
    assert any("could not be loaded" in c for c in report.narrative.caveats)


def test_site_yield_failure_falls_back_to_county_yield(sources, settings, monkeypatch):
    calls = []

    async def centroid_only(client, s, lat, lon):
        calls.append((lat, lon))
        if (lat, lon) != (COUNTY.centroid_lat, COUNTY.centroid_lon):
            raise SourceError("PVWatts rate limit reached")
        return 1000.0, False

    monkeypatch.setattr(pvwatts, "fetch_yield", centroid_only)
    report = analyze(settings)

    solar = next(o for o in report.opportunities if o.solution_id == "community_solar")
    energy = next(e for e in solar.estimates if e.label == "Annual energy")
    assert energy.value == 6000.0
    assert any("county center, not the site" in a for a in energy.assumptions)
    assert len(calls) <= 1 + settings.max_pvwatts_sites


def test_map_caps_keep_opportunity_assets(sources, settings, monkeypatch):
    rows = [
        {
            "site_id": i,
            "name": f"Site {i}",
            "site_type": "brownfield",
            "program": "BROWNFIELDS",
            "lat": 0.5,
            "lon": 0.5,
            "acres": float(i),
            "pv_scale": None,
        }
        for i in range(1, 11)
    ]
    # The only screened site is the smallest, so the cap alone would drop it.
    rows[0]["pv_scale"] = "utility"
    monkeypatch.setattr(repowering, "sites_for_county", lambda data_dir, fips: rows)
    capped = settings.model_copy(update={"max_sites_per_county": 3})

    report = analyze(capped)
    shown = [a.id for a in report.assets if a.type == "brownfield"]

    assert report.asset_counts["brownfield"] == 10
    assert shown[:3] == ["repowering:10", "repowering:9", "repowering:8"]  # largest first
    assert "repowering:1" in shown and len(shown) == 4
