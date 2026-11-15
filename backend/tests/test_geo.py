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


def test_nearest_to_center_first_and_truncated(grid_index):
    # Covers all of row 0 and the lower 60% of row 1: six counties match.
    # The center is (1.5, 0.8): 99002 (0.3 away) and 99005 (0.7) are nearest,
    # then 99001 and 99003 (about 1.04). 99005 beats the fully covered 99001
    # and 99003 even though only 60% of it is in the box.
    counties, truncated = grid_index.resolve_bbox((0.0, 0.0), (3.0, 1.6), 4)
    assert truncated is True
    assert fips_of(counties) == ["99002", "99005", "99001", "99003"]


def test_far_county_with_more_overlap_loses_to_a_near_one():
    # A big county (0-4 square, centroid (2, 2)) and a small one beside it
    # (4-5 by 1.5-2.5, centroid (4.5, 2)). The box x 1.8-5, y 1-3 holds both
    # centroids; its center is (3.4, 2). The big county overlaps 4.4 square
    # degrees and is 1.4 away; the small one overlaps 1.0 and is 1.1 away.
    index = CountyIndex([make_county("99010", 0, 0, 4, 4), make_county("99011", 4, 1.5, 5, 2.5)])
    counties, truncated = index.resolve_bbox((1.8, 1.0), (5.0, 3.0), 1)
    assert fips_of(counties) == ["99011"] and truncated is True


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
