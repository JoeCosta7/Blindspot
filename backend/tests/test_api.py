import time

import pytest
from fastapi.testclient import TestClient

from app import cache, main
from app.models import CountyContext, CountyReport, Narrative, Resources
from app.sources import SourceError, census
from tests.conftest import make_metrics


def minimal_report(county) -> CountyReport:
    return CountyReport(
        fips=county.fips,
        name=county.name,
        state=county.state,
        population=None,
        context=CountyContext(),
        needs=[],
        assets=[],
        resources=Resources(),
        opportunities=[],
        narrative=Narrative(summary="s", opportunity_notes=[], caveats=[], generated_by="template"),
        source_status=[],
    )


@pytest.fixture
def client(grid_index, settings, monkeypatch, tmp_path):
    analyzed = []

    async def fake_analyze(county, http_client, s):
        analyzed.append(county.fips)
        return minimal_report(county)

    monkeypatch.setattr(main, "analyze_county", fake_analyze)
    with TestClient(main.app) as test_client:
        main.app.state.counties = grid_index
        main.app.state.settings = settings.model_copy(update={"data_dir": tmp_path})
        test_client.analyzed = analyzed
        yield test_client


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["counties_loaded"] == 6
    assert body["keys"] == {"census": False, "nlr": False, "gemini": False}


def test_analyze_county(client):
    resp = client.post("/api/analyze", json={"mode": "county", "fips": "99002"})
    assert resp.status_code == 200
    body = resp.json()
    assert [c["fips"] for c in body["counties"]] == ["99002"]
    assert body["truncated"] is False and body["message"] is None


def test_unknown_county_is_404(client):
    resp = client.post("/api/analyze", json={"mode": "county", "fips": "12345"})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "county", "fips": "123"},
        {"mode": "county"},
        {"mode": "bbox", "sw": [0, 0]},
        {"mode": "other", "fips": "99001"},
    ],
)
def test_bad_requests_are_422(client, body):
    assert client.post("/api/analyze", json=body).status_code == 422


def test_bbox_truncates_with_a_notice_instead_of_an_error(client):
    resp = client.post("/api/analyze", json={"mode": "bbox", "sw": [0, 0], "ne": [3, 2]})
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["counties"]) == 4
    assert body["truncated"] is True
    assert "zoom in" in body["message"]
    assert len(client.analyzed) == 4


def test_bbox_over_nothing(client):
    body = client.post("/api/analyze", json={"mode": "bbox", "sw": [50, 50], "ne": [51, 51]}).json()
    assert body["counties"] == [] and "No county matched" in body["message"]


def test_fixture_mode_serves_saved_reports_without_analysis(client, grid_index, tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    saved = minimal_report(grid_index.get("99001"))
    (fixtures / "99001.json").write_text(saved.model_dump_json())

    hit = client.post("/api/analyze?fixture=1", json={"mode": "county", "fips": "99001"})
    miss = client.post("/api/analyze?fixture=1", json={"mode": "county", "fips": "99002"})

    assert hit.status_code == 200 and hit.json()["counties"][0]["fips"] == "99001"
    assert miss.status_code == 404
    assert client.analyzed == []


def test_saved_reports_are_served_and_other_counties_run_live(client, grid_index, tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    saved = minimal_report(grid_index.get("99001"))
    saved.narrative.summary = "saved earlier"
    (fixtures / "99001.json").write_text(saved.model_dump_json())
    (fixtures / "99003.json").write_text('{"fips": "99003"}')  # no longer fits the contract

    def summary_for(fips: str) -> str:
        resp = client.post("/api/analyze", json={"mode": "county", "fips": fips})
        assert resp.status_code == 200
        return resp.json()["counties"][0]["narrative"]["summary"]

    # Off by default: a saved file changes nothing.
    assert summary_for("99001") == "s"
    assert client.analyzed == ["99001"]

    client.analyzed.clear()
    main.app.state.settings = main.app.state.settings.model_copy(
        update={"serve_saved_reports": True}
    )
    assert summary_for("99001") == "saved earlier"
    assert summary_for("99002") == "s"  # no file
    assert summary_for("99003") == "s"  # unusable file
    assert client.analyzed == ["99002", "99003"]


def test_a_box_mixes_saved_and_live_counties(client, grid_index, tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "99001.json").write_text(minimal_report(grid_index.get("99001")).model_dump_json())
    main.app.state.settings = main.app.state.settings.model_copy(
        update={"serve_saved_reports": True}
    )

    body = client.post("/api/analyze", json={"mode": "bbox", "sw": [0, 0], "ne": [2, 1]}).json()

    assert sorted(c["fips"] for c in body["counties"]) == ["99001", "99002"]
    assert client.analyzed == ["99002"]


def test_counties_geojson_missing_is_503(client):
    assert client.get("/api/counties.geojson").status_code == 503


# --- cache ----------------------------------------------------------------------


def test_cache_round_trip_ttl_and_disk(tmp_path):
    cache.clear_memory()
    args = {"state": "VT"}
    assert cache.cache_get(tmp_path, "src", args, 60) is None

    cache.cache_set(tmp_path, "src", args, {"n": 1})
    assert cache.cache_get(tmp_path, "src", args, 60) == {"n": 1}
    assert cache.cache_get(tmp_path, "src", {"state": "NH"}, 60) is None

    cache.clear_memory()  # a new process still finds it on disk
    assert cache.cache_get(tmp_path, "src", args, 60) == {"n": 1}

    time.sleep(0.02)
    assert cache.cache_get(tmp_path, "src", args, 0.01) is None  # expired


def test_need_map_scores_every_county_like_a_report(client, monkeypatch):
    async def all_counties(*args):
        return {
            # income 40,000 against 80,000; 70 of 100 homes rented against 35%
            "99001": {
                "B19013_001E": 40_000.0,
                "B25003_001E": 100.0,
                "B25003_003E": 70.0,
            },
            "99002": {"B19013_001E": None, "B25003_001E": 100.0, "B25003_003E": 35.0},
        }, True

    async def national(*args):
        return make_metrics(), True

    monkeypatch.setattr(census, "fetch_all_counties", all_counties)
    monkeypatch.setattr(census, "fetch_national", national)

    body = client.get("/api/need-map").json()
    layers = {layer["id"]: layer for layer in body["layers"]}

    assert body["ratio_breaks"] == [1.0, 1.1, 1.25, 1.5]
    assert set(layers) == {"low_income", "renters"}  # no county has the other figures
    income = layers["low_income"]
    assert income["lower_is_worse"] is True and income["national_value"] == 80_000.0
    assert income["counties"] == {"99001": [40_000.0, 2.0]}  # no figure, no entry
    assert layers["renters"]["counties"] == {"99001": [0.7, 2.0], "99002": [0.35, 1.0]}


def test_need_map_is_503_when_census_fails(client, monkeypatch):
    async def down(*args):
        raise SourceError("CENSUS_API_KEY is not set")

    monkeypatch.setattr(census, "fetch_all_counties", down)
    resp = client.get("/api/need-map")
    assert resp.status_code == 503 and "CENSUS_API_KEY" in resp.json()["detail"]
