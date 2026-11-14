"""Site Scout with an in-memory provider and a scripted decider. No network."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.estimates import rooftop_solar
from app.geo import County
from app.models import ScoutResult
from app.scout import agent, measure
from app.scout.agent import AgentAction
from app.scout.provider import Building, Detection, InteriorRecord, RoofRecord, SitePicture
from app.sources import SourceError
from tests.conftest import make_county, make_need
from tests.test_api import minimal_report

# A 100 m x 100 m footprint near downtown Houston, and a 10 m x 10 m box on it.
FOOTPRINT = (
    "POLYGON((-95.3870 29.7740,-95.3860 29.7740,-95.3860 29.7749,"
    "-95.3870 29.7749,-95.3870 29.7740))"
)
HVAC = (
    "POLYGON((-95.3866 29.7744,-95.3865 29.7744,-95.3865 29.7745,"
    "-95.3866 29.7745,-95.3866 29.7744))"
)
PANEL = (
    "POLYGON((-95.3868 29.7742,-95.3867 29.7742,-95.3867 29.7743,"
    "-95.3868 29.7743,-95.3868 29.7742))"
)
PARCEL = (
    "POLYGON((-95.3875 29.7735,-95.3855 29.7735,-95.3855 29.7755,"
    "-95.3875 29.7755,-95.3875 29.7735))"
)

HAZARD = make_need("hazard_risk", 100).model_copy(
    update={"detail": "Heat wave: Very High; Inland flooding: Very High", "theme": "resilience"}
)
NEEDS = [HAZARD, make_need("renters", 60)]


def roof(**overrides) -> RoofRecord:
    base = dict(
        parcel_wkt=PARCEL,
        footprint_wkt=FOOTPRINT,
        footprint_m2=measure.wkt_area_m2(FOOTPRINT),
        building_type="commercial",
        roof_shape="flat",
        roof_material="membrane",
        roof_condition=4.0,
        roof_tree_cover_pct=0.0,
        solar_present=False,
        ac_units=3,
        parcel_tree_cover_pct=2.0,
        hardscape_m2=5000.0,
        vehicles_count=4,
        gsd_m=0.075,
        captured="2026-01-05",
        collection="us-tx-houston-2026",
        inside_parcel=True,
        detections=(
            Detection("rooftop hvac unit", HVAC, 0.9),
            Detection("car", HVAC, 0.9),
            Detection("car", HVAC, 0.8),
            Detection("car", HVAC, 0.8),
        ),
    )
    base.update(overrides)
    return RoofRecord(**base)


BUILDINGS = [
    Building(
        "mp:A",
        "Warehouse A",
        29.7745,
        -95.3865,
        "matterport",
        "1 Main St",
        scan_id="A",
        floors=1,
        rooms=3,
    ),
    Building(
        "mp:B",
        "Store B",
        29.7800,
        -95.3900,
        "matterport",
        "2 Oak St",
        scan_id="B",
        floors=1,
        rooms=2,
    ),
    Building(
        "mp:C",
        "Office C",
        29.7900,
        -95.4000,
        "matterport",
        None,
        scan_id=None,
        floors=None,
        rooms=None,
    ),
]
# A CoStar record whose gross area over its storeys is far larger than the footprint.
COSTAR_D = Building(
    "cs:9",
    "Depot D",
    29.7745,
    -95.3865,
    "costar",
    "9 Rail Rd",
    scan_id=None,
    costar_id=9,
    property_type="Industrial",
    property_subtype="Industrial_Warehouse",
    gross_sqft=400_000.0,
    stories=1,
    year_built=1992,
    parking_spaces=120,
)
ROOFS = {
    "mp:A": roof(),
    "mp:B": roof(solar_present=True, detections=(Detection("solar panel", PANEL, 0.9),)),
    "mp:C": roof(),
    "cs:9": roof(),
}
INTERIORS = {
    "A": InteriorRecord(
        "A",
        "Warehouse A",
        (("Floor 1", 8000.0),),
        (("Warehouse", 7900.0),),
        8000.0,
        "factory_industrial",
        "A big open warehouse.",
        "https://my.matterport.com/show/?m=A",
        None,
    ),
    "B": InteriorRecord(
        "B", "Store B", (("Floor 1", 2000.0),), (), 2000.0, "retail", None, None, None
    ),
}


class FakeProvider:
    name = "fake"

    def __init__(self, fail_roof: bool = False) -> None:
        self.fail_roof = fail_roof

    async def scanned_buildings(self, county: County) -> list[Building]:
        return BUILDINGS

    async def buildings_from_above(self, county, lat, lon, half_m, text, limit):
        return []

    async def properties(self, county: County) -> list[Building]:
        return [COSTAR_D]

    async def roof(self, building: Building) -> RoofRecord | None:
        if self.fail_roof:
            raise SourceError("Vexcel is down")
        return ROOFS[building.id]

    async def interior(self, building: Building) -> InteriorRecord | None:
        return INTERIORS.get(building.scan_id or "")

    async def image(self, building: Building, roof: RoofRecord):
        name = f"img_{building.id[-1]}"
        return SitePicture(name, "oblique", name, ["oblique-south", "oblique-north"])


def scripted(actions: list[AgentAction]):
    queue = list(actions)

    async def decide(prompt: str) -> AgentAction:
        return queue.pop(0) if queue else AgentAction(thought="", tool="finish")

    return decide


async def collect(settings, decider, provider=None, needs=NEEDS):
    county = make_county("48201", -96, 29, -95, 30)
    events = []
    async for event in agent.run_scout(
        county, needs, 1437.0, provider or FakeProvider(), settings, None, decider
    ):
        events.append(event)
    return events


# --- measurements -----------------------------------------------------------------------


def test_geodesic_area_and_obstruction_clipping():
    footprint = measure.wkt_area_m2(FOOTPRINT)
    assert 9_500 < footprint < 10_500  # about 96 m x 100 m at this latitude
    hvac = [Detection("rooftop hvac unit", HVAC, 0.9)] * 2  # the same box twice counts once
    covered = measure.obstruction_m2(hvac, FOOTPRINT)
    assert 90 < covered < 120
    assert measure.obstruction_m2(
        [Detection("rooftop hvac unit", PARCEL, 0.9)], FOOTPRINT
    ) == pytest.approx(footprint, rel=0.01)  # a box larger than the roof is clipped to it


def test_footprint_check_passes_near_and_fails_far():
    r = roof()
    close = INTERIORS["A"].__class__(
        "A", None, (("Floor 1", r.footprint_m2 * 0.85),), (), None, None, None, None, None
    )
    far = INTERIORS["A"].__class__(
        "A", None, (("Floor 1", r.footprint_m2 * 0.3),), (), None, None, None, None, None
    )
    assert measure.footprint_check(r, close, 0.3).ok is True
    assert measure.footprint_check(r, far, 0.3).ok is False
    assert measure.footprint_check(r, None, 0.3) is None


def test_rooftop_solar_estimate_is_labelled():
    figures = rooftop_solar(10_000.0, 100.0, 1437.0, True)
    by_label = {e.label: e for e in figures}
    assert set(by_label) == {"Capacity", "Annual energy", "Household equivalent"}
    assert by_label["Capacity"].value == pytest.approx(9_900 * 0.85 / 27_923 * 1000, rel=0.02)
    assert all(e.is_estimate and e.assumptions for e in figures)


# --- the agent --------------------------------------------------------------------------


def test_scripted_run_shortlists_rejects_and_flags_invalid_actions(settings):
    actions = [
        AgentAction(
            thought="Evaluate first", tool="evaluate_site", building_id="mp:A"
        ),  # invalid: nothing seen
        AgentAction(
            thought="Look for warehouses and stores",
            tool="search_scans",
            keywords=["warehouse", "store", "office"],
        ),
        AgentAction(thought="Roof of A", tool="inspect_roof", building_id="mp:A"),
        AgentAction(thought="Roof of B", tool="inspect_roof", building_id="mp:B"),
        AgentAction(thought="Roof of C", tool="inspect_roof", building_id="mp:C"),
        AgentAction(
            thought="A fits with 999 panels",
            tool="evaluate_site",
            building_id="mp:A",
            solution_ids=["rooftop_solar_commercial", "cooling_center"],
            note="Flat roof, open interior, 8,000 m² floor.",
        ),
        AgentAction(
            thought="B",
            tool="evaluate_site",
            building_id="mp:B",
            solution_ids=["rooftop_solar_commercial"],
        ),
        AgentAction(
            thought="C",
            tool="evaluate_site",
            building_id="mp:C",
            solution_ids=["rooftop_solar_commercial"],
        ),
        AgentAction(thought="Done", tool="finish"),
    ]
    events = asyncio.run(collect(settings, scripted(actions)))
    result = events[-1].result
    assert result is not None and result.generated_by == "gemini"
    by_id = {c.building_id: c for c in result.candidates}

    a, b, c = by_id["mp:A"], by_id["mp:B"], by_id["mp:C"]
    assert a.status == "shortlisted" and set(a.solution_ids) == {
        "rooftop_solar_commercial",
        "cooling_center",
    }
    assert a.layers_used == ["vexcel", "matterport"]
    assert {e.label for e in a.estimates} == {"Capacity", "Annual energy", "Household equivalent"}
    assert (
        a.agent_note == "Flat roof, open interior, 8,000 m² floor."
    )  # 8,000 appears in the scan text
    assert any("flooding" in f for f in a.flags)
    assert a.image_url == "/api/scout/image/img_A" and a.tour_url is not None
    assert [o.url for o in a.image_options] == [
        "/api/scout/oblique/img_A/oblique-south",
        "/api/scout/oblique/img_A/oblique-north",
    ]
    assert any(ch.label.startswith("Scan floor area") and ch.ok for ch in a.checks)

    assert b.status == "rejected" and "already has solar" in b.status_reason
    # No scan: still shortlisted on the aerial record alone, but marked and scored lower.
    assert c.status == "shortlisted" and "no 3D scan" in c.status_reason and c.score < a.score

    kinds = [s.kind for s in result.steps]
    assert kinds[0] == "plan" and "error" in kinds  # the invalid first action
    # A valid thought is kept; the one with an invented number (999) is replaced by code text.
    actions = [s.text for s in result.steps if s.kind == "action"]
    assert actions[1] == "Roof of A" and not any("999" in text for text in actions)
    # "999" is not in the state, so the thought was replaced by the code text
    assert not any("999" in s.text for s in result.steps)
    assert result.drafts and result.drafts[0].building_id == "mp:A"
    assert result.stopped_because == "no_more_candidates"


def test_model_failure_falls_back_to_rules(settings):
    async def broken(prompt: str) -> AgentAction:
        raise RuntimeError("503")

    events = asyncio.run(collect(settings, broken))
    result = events[-1].result
    assert result.generated_by == "rules"
    assert {c.building_id for c in result.candidates} == {"cs:9", "mp:A", "mp:B", "mp:C"}
    assert any(s.source == "scout_gemini" and not s.ok for s in result.source_status)


def test_failed_layer_does_not_fail_the_run(settings):
    actions = [
        AgentAction(thought="", tool="search_scans", keywords=["warehouse"]),
        AgentAction(thought="", tool="inspect_roof", building_id="mp:A"),
        AgentAction(thought="", tool="evaluate_site", building_id="mp:A"),
    ]
    events = asyncio.run(collect(settings, scripted(actions), FakeProvider(fail_roof=True)))
    result = events[-1].result
    assert any(s.source == "vexcel" and not s.ok for s in result.source_status)
    assert result.candidates[0].status == "low_confidence" and result.candidates[0].layers_used == [
        "matterport"
    ]


# --- the endpoint ----------------------------------------------------------------------


def test_scout_endpoint_streams_ndjson(grid_index, settings, monkeypatch, tmp_path):
    async def fake_analyze(county, client, s):
        report = minimal_report(county)
        return report.model_copy(update={"needs": NEEDS})

    monkeypatch.setattr(main, "analyze_county", fake_analyze)
    monkeypatch.setattr(main.scout_provider, "get_provider", lambda client, s: FakeProvider())
    monkeypatch.setattr(
        agent,
        "gemini_decider",
        lambda s: _ready(
            scripted(
                [
                    AgentAction(thought="", tool="search_scans", keywords=["warehouse"]),
                    AgentAction(thought="", tool="inspect_roof", building_id="mp:A"),
                    AgentAction(thought="", tool="evaluate_site", building_id="mp:A"),
                ]
            )
        ),
    )
    with TestClient(main.app) as client:
        main.app.state.counties = grid_index
        main.app.state.settings = settings.model_copy(update={"scout_counties": ["99001"]})
        assert client.post("/api/scout", json={"fips": "99002"}).status_code == 404
        resp = client.post("/api/scout", json={"fips": "99001"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in resp.text.strip().splitlines()]
    assert lines[0]["type"] == "step" and lines[-1]["type"] == "result"
    result = ScoutResult.model_validate(lines[-1]["result"])
    assert result.fips == "99001" and result.candidates[0].building_id == "mp:A"
    assert [s.index for s in result.steps] == list(range(len(result.steps)))


async def _ready(decider):
    return decider


def test_property_records_are_a_layer_and_cross_checked(settings):
    actions = [
        AgentAction(
            thought="", tool="search_properties", property_types=["warehouse"], max_stories=1
        ),
        AgentAction(thought="", tool="inspect_roof", building_id="cs:9"),
        AgentAction(thought="", tool="evaluate_site", building_id="cs:9"),
    ]
    events = asyncio.run(collect(settings, scripted(actions)))
    result = events[-1].result
    d = result.candidates[0]
    assert d.building_id == "cs:9" and d.layers_used == ["costar", "vexcel"]
    assert {o.label for o in d.observations if o.layer == "costar"} >= {
        "Gross building area",
        "Year built",
    }
    record = next(c for c in d.checks if c.label == "Record floor area vs footprint")
    assert record.ok is False  # 400,000 sq ft on one storey vs a ~10,000 m² footprint
    assert d.status == "low_confidence"
    assert any(s.source == "costar" and s.ok for s in result.source_status)


def test_costar_kit_loads_when_present(settings):
    from app.sources import costar

    if not costar.available(settings.costar_kit_dir):
        pytest.skip("CoStar kit not installed")
    records = costar.load_properties(settings.costar_kit_dir)
    assert len(records) > 3000
    r = next(x for x in records if x["property_type"] == "Industrial")
    assert r["lat"] and r["lon"] and r["gross_sqft"] and r["name"]
    harris = make_county("48201", -95.9, 29.5, -94.9, 30.2)
    assert len(costar.properties_in_county(settings.costar_kit_dir, harris)) > 1000
