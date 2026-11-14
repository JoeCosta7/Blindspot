from app.geo import CountyIndex
from tests.conftest import make_county


def fips_of(counties):
    return [c.fips for c in counties]


def test_lookup_by_fips(grid_index):
    assert grid_index.get("99001").name == "County 99001"
    assert grid_index.get("00000") is None
    assert len(grid_index) == 6


def test_box_inside_one_county_selects_it_by_area_rule(grid_index):
    # Covers 30% of 99001 (x 0.1-0.7, y 0.1-0.6) but not its centroid... the
    # centroid (0.5, 0.5) is inside, so it is kept either way.
    counties, truncated = grid_index.resolve_bbox((0.1, 0.1), (0.7, 0.6), 4)
    assert fips_of(counties) == ["99001"]
    assert truncated is False


def test_small_overlap_without_centroid_is_dropped(grid_index):
    # 20% of 99001 and no centroid: below the 25% area rule.
    counties, _ = grid_index.resolve_bbox((0.0, 0.0), (0.4, 0.5), 4)
    assert counties == []


def test_quarter_of_area_without_centroid_is_kept(grid_index):
    # x 0-0.45, y 0-0.6 = 27% of 99001; centroid (0.5, 0.5) is outside.
    counties, _ = grid_index.resolve_bbox((0.0, 0.0), (0.45, 0.6), 4)
    assert fips_of(counties) == ["99001"]


def test_sorted_by_overlap_and_truncated(grid_index):
    # Covers all of row 0 and the lower 60% of row 1: six counties match.
    counties, truncated = grid_index.resolve_bbox((0.0, 0.0), (3.0, 1.6), 4)
    assert truncated is True
    assert len(counties) == 4
    # The three fully covered counties come first.
    assert set(fips_of(counties[:3])) == {"99001", "99002", "99003"}


def test_not_truncated_at_exactly_the_limit(grid_index):
    counties, truncated = grid_index.resolve_bbox((0.0, 0.0), (2.0, 2.0), 4)
    assert set(fips_of(counties)) == {"99001", "99002", "99004", "99005"}
    assert truncated is False


def test_corners_in_any_order(grid_index):
    a, _ = grid_index.resolve_bbox((0.1, 0.1), (0.9, 0.9), 4)
    b, _ = grid_index.resolve_bbox((0.9, 0.9), (0.1, 0.1), 4)
    assert fips_of(a) == fips_of(b) == ["99001"]


def test_box_over_nothing(grid_index):
    counties, truncated = grid_index.resolve_bbox((50.0, 50.0), (51.0, 51.0), 4)
    assert counties == [] and truncated is False


def test_contains_points():
    county = make_county("99001", 0, 0, 1, 1)
    mask = county.contains_points([0.5, 1.5, 0.2], [0.5, 0.5, 0.9])
    assert mask.tolist() == [True, False, True]
    assert county.contains_points([], []).tolist() == []


def test_empty_index():
    index = CountyIndex([])
    assert len(index) == 0
    assert index.resolve_bbox((0, 0), (1, 1), 4) == ([], False)
