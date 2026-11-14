from app import peers
from app.sources import census
from tests.conftest import make_metrics, make_need


def values(population: float | None, income: float | None) -> census.Values:
    return {"B01003_001E": population, "B19013_001E": income}


DIRECTORY = {
    code: peers.CountyInfo(f"County {code}", "ZZ", 100.0)
    for code in ("00001", "00002", "00003", "00004", "00005")
}
ALL = {
    "00001": values(10_000, 50_000),
    "00002": values(11_000, 52_000),  # closest to 00001
    "00003": values(40_000, 60_000),
    "00004": values(2_000_000, 120_000),
    "00005": values(10_000, None),  # no income published: never a peer
}


def test_nearest_orders_by_similarity_and_leaves_out_the_county_itself():
    assert peers.nearest("00001", ALL, DIRECTORY, 3) == ["00002", "00003", "00004"]
    assert peers.nearest("00001", ALL, DIRECTORY, 1) == ["00002"]


def test_density_separates_counties_of_the_same_size_and_income():
    everyone = {code: values(10_000, 50_000) for code in ("00001", "00002", "00003")}
    directory = {
        "00001": peers.CountyInfo("A", "ZZ", 100.0),
        "00002": peers.CountyInfo("B", "ZZ", 5_000.0),
        "00003": peers.CountyInfo("C", "ZZ", 120.0),
    }
    assert peers.nearest("00001", everyone, directory, 2) == ["00003", "00002"]


def test_no_peers_for_a_county_missing_a_figure_or_not_in_the_data():
    assert peers.nearest("00005", ALL, DIRECTORY, 3) == []
    assert peers.nearest("99999", ALL, DIRECTORY, 3) == []
    no_area = {**DIRECTORY, "00001": peers.CountyInfo("A", "ZZ", None)}
    assert peers.nearest("00001", ALL, no_area, 3) == []


def test_find_peers_marks_shared_needs_first(settings):
    everyone = {
        "00001": values(10_000, 50_000),
        "00002": {
            **values(11_000, 52_000),
            "B25003_001E": 100.0,
            "B25003_003E": 70.0,  # renters 70% against 35% nationally
        },
    }
    national = make_metrics()  # income 80,000
    flagged = [make_need("low_income", 80)]

    [peer] = peers.find_peers("00001", everyone, DIRECTORY, national, flagged, settings)

    assert peer.people_per_sq_mile == 110.0
    assert [(n.id, n.shared) for n in peer.needs] == [("low_income", True), ("renters", False)]


def test_find_peers_without_national_figures_lists_no_needs(settings):
    [peer, *_] = peers.find_peers("00001", ALL, DIRECTORY, None, [], settings)
    assert peer.fips == "00002" and peer.needs == []


def test_parse_all_counties_keys_rows_by_fips():
    header = ["NAME", "B01003_001E", "B19013_001E", "state", "county"]
    body = [
        header,
        ["Autauga County, Alabama", "59947", "72481", "01", "001"],
        ["Loving County, Texas", "64", "-666666666", "48", "301"],
    ]
    parsed = census.parse_all_counties(body)
    assert parsed["01001"] == {"B01003_001E": 59947.0, "B19013_001E": 72481.0}
    assert parsed["48301"]["B19013_001E"] is None


def test_directory_reads_the_real_county_file(settings):
    directory = peers.load_directory(settings.data_dir / "counties.geojson")
    autauga = directory["01001"]
    assert (autauga.name, autauga.state) == ("Autauga County", "AL")
    assert 590 < autauga.land_sq_miles < 600  # 1,539.6 sq km
