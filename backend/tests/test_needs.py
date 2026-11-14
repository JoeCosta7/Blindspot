import pytest

from app import needs
from tests.conftest import make_metrics


@pytest.mark.parametrize(
    ("ratio", "severity", "level"),
    [
        (0.90, 0, None),
        (1.00, 0, None),
        (1.09, 18, None),
        (1.10, 20, "elevated"),
        (1.24, 48, "elevated"),
        (1.25, 50, "high"),
        (1.50, 100, "high"),
        (3.00, 100, "high"),
    ],
)
def test_score_ratio(settings, ratio, severity, level):
    assert needs.score_ratio(ratio, settings) == (severity, level)


def test_acs_needs_flags_only_elevated_or_high(settings):
    national = make_metrics()
    county = make_metrics(
        pre1980_housing_share=0.75,  # ratio 1.5 -> high
        renter_share=0.40,  # ratio 1.14 -> elevated
        no_vehicle_share=0.08,  # ratio 1.0 -> not returned
    )
    found = {n.id: n for n in needs.acs_needs(county, national, settings)}

    assert set(found) == {"old_housing", "renters"}
    assert (found["old_housing"].level, found["old_housing"].severity) == ("high", 100)
    assert found["renters"].level == "elevated"
    assert found["old_housing"].national_value == 0.50
    assert found["old_housing"].source == "ACS 2020-2024"


def test_low_income_ratio_is_inverted(settings):
    national = make_metrics(median_household_income=80_000.0)
    poorer = needs.acs_needs(make_metrics(median_household_income=60_000.0), national, settings)
    richer = needs.acs_needs(make_metrics(median_household_income=120_000.0), national, settings)

    low = next(n for n in poorer if n.id == "low_income")
    assert low.level == "high"  # 80,000 / 60,000 = 1.33
    assert low.severity == 67
    assert (low.value, low.national_value, low.unit) == (60_000.0, 80_000.0, "usd")
    assert not any(n.id == "low_income" for n in richer)


def test_acs_needs_skip_missing_values(settings):
    national = make_metrics()
    county = make_metrics(pre1980_housing_share=None, median_household_income=None)
    ids = {n.id for n in needs.acs_needs(county, national, settings)}
    assert "old_housing" not in ids
    assert "low_income" not in ids


def test_ev_need_zero_stations_is_high_without_a_benchmark(settings):
    need = needs.ev_need(0, None, None, settings)
    assert need is not None
    assert (need.level, need.severity, need.value) == ("high", 100, 0.0)


def test_ev_need_compares_rate_to_benchmark(settings):
    # 5 stations per 100,000 people = 0.5 per 10k, against a benchmark of 2.0.
    need = needs.ev_need(5, 100_000, 2.0, settings)
    assert need is not None
    assert need.level == "high"
    assert need.value == pytest.approx(0.5)
    assert need.national_value == 2.0

    assert needs.ev_need(30, 100_000, 2.0, settings) is None  # above benchmark
    assert needs.ev_need(5, None, 2.0, settings) is None  # nothing to compare
    assert needs.ev_need(5, 100_000, None, settings) is None


def test_ev_benchmark(settings):
    assert needs.ev_benchmark_per_10k(80_000, 320_000_000, settings) == pytest.approx(2.5)
    assert needs.ev_benchmark_per_10k(None, 320_000_000, settings) is None
    fixed = settings.model_copy(update={"ev_benchmark_per_10k": 3.0})
    assert needs.ev_benchmark_per_10k(None, None, fixed) == 3.0


def test_landfill_need_requires_a_candidate(settings):
    rows = [
        {"name": "Operating LF", "is_candidate": False},
        {"name": "County LF", "is_candidate": True},
    ]
    need = needs.landfill_need(rows, settings)
    assert need is not None
    assert (need.level, need.value, need.unit) == ("high", 1.0, "count")
    assert "County LF" in need.detail
    assert needs.landfill_need(rows[:1], settings) is None
    assert needs.landfill_need([], settings) is None


def test_hazard_need_levels_and_detail(settings):
    ratings = {
        "nri_version": "December 2025",
        "inland_flood_rating": "Relatively Moderate",
        "wildfire_rating": "Very High",
        "drought_rating": "Relatively Low",
        "heat_wave_rating": "No Rating",
    }
    need = needs.hazard_need(ratings, settings)
    assert need is not None
    assert (need.level, need.severity) == ("high", 100)
    assert need.detail == "Wildfire: Very High; Inland flooding: Relatively Moderate"
    assert "December 2025" in need.source

    moderate = needs.hazard_need({"drought_rating": "Relatively Moderate"}, settings)
    assert moderate is not None and moderate.level == "elevated"

    assert needs.hazard_need({"wildfire_rating": "Relatively Low"}, settings) is None
    assert needs.hazard_need(None, settings) is None
