import time

import pytest
from fastapi.testclient import TestClient

from app import cache, main
from app.models import CountyContext, CountyReport, Narrative, Resources


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
