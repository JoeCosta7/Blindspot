import pytest

from app.profile import acs_profile, format_profile_value
from tests.conftest import make_metrics


def test_acs_profile_compares_to_national(settings):
    county = make_metrics(trades_occupation_share=0.18, median_age=46.0)
    national = make_metrics(trades_occupation_share=0.09, median_age=39.2)

    facts = {f.id: f for f in acs_profile(county, national, settings)}

    trades = facts["trades_workers"]
    assert trades.category == "workforce"
    assert trades.value == pytest.approx(0.18)
    assert trades.national_value == pytest.approx(0.09)
    assert trades.unit == "share"
    assert trades.source == settings.acs_label
    assert facts["median_age"].unit == "years"


def test_acs_profile_skips_missing_figures_and_national(settings):
    county = make_metrics(trades_occupation_share=0.18)  # every other profile figure None
    facts = acs_profile(county, None, settings)
    assert [f.id for f in facts] == ["trades_workers"]
    assert facts[0].national_value is None


def test_format_profile_value():
    assert format_profile_value(0.1834, "share") == "18.3%"
    assert format_profile_value(46.04, "years") == "46.0 years"
    assert format_profile_value(None, "share") is None


def test_climate_summary_math():
    from app.sources.openmeteo import summarize

    # Two days: 0 C (32 F) and 30 C (86 F); highs 35 C (95 F) and 20 C.
    daily = {
        "temperature_2m_mean": [0.0, 30.0, None],
        "temperature_2m_max": [35.0, 20.0],
        "precipitation_sum": [25.4, 0.0],
        "snowfall_sum": [2.54, 0.0],
    }
    s = summarize(daily, [5.0, 7.0, None])
    per_year = 365.25 / 2
    assert s["heating_degree_days"] == pytest.approx(33 * per_year)  # 65 - 32
    assert s["cooling_degree_days"] == pytest.approx(21 * per_year)  # 86 - 65
    assert s["hot_days"] == pytest.approx(1 * per_year)
    assert s["precipitation_in"] == pytest.approx(1 * per_year)
    assert s["snowfall_in"] == pytest.approx(1 * per_year)
    assert s["wind_speed_100m"] == pytest.approx(6.0)


def test_climate_summary_leaves_out_missing_series():
    from app.sources.openmeteo import summarize

    assert summarize({"precipitation_sum": [None]}, None) == {}


def test_climate_profile_facts():
    from app.profile import climate_profile

    facts = climate_profile({"heating_degree_days": 6789.4, "hot_days": 3.2})
    assert [f.id for f in facts] == ["heating_degree_days", "hot_days"]
    assert all(f.category == "climate" and f.national_value is None for f in facts)
    assert climate_profile(None) == []
    assert format_profile_value(6789.4, "degree_days_f") == "6,789 degree days"


def test_svi_profile():
    from app.profile import svi_profile

    row = {"svi_overall": 0.7248, "svi_household": None, "age65_pct": 19.7}
    facts = {f.id: f for f in svi_profile(row)}
    assert set(facts) == {"svi_overall", "age_65_plus"}
    assert facts["svi_overall"].unit == "percentile"
    assert facts["age_65_plus"].value == pytest.approx(0.197)  # percent -> share
    assert svi_profile(None) == []
    assert format_profile_value(0.7248, "percentile") == "72nd percentile"
    assert format_profile_value(0.111, "percentile") == "11th percentile"
    assert format_profile_value(0.01, "percentile") == "1st percentile"


def test_land_profile():
    from app.profile import land_profile

    metrics = {
        "heat_island_c": 10.2,  # a need input, not a profile fact
        "forest_share": 0.756,
        "flat_open_share": 0.14,
        "elevation_mean_m": 408.0,
        "elevation_min_m": 247.0,
        "elevation_max_m": 639.0,
        "slope_deg": 5.4,
        "water_share": None,
    }
    facts = {f.id: f for f in land_profile(metrics)}
    assert set(facts) == {
        "forest",
        "flat_open_land",
        "elevation_mean",
        "elevation_range",
        "slope_mean",
    }
    assert all(f.category == "geography" for f in facts.values())
    assert facts["elevation_mean"].value == pytest.approx(408 * 3.28084)
    assert facts["elevation_range"].value == pytest.approx(392 * 3.28084)
    assert format_profile_value(1338.6, "feet") == "1,339 feet"
    assert land_profile(None) == []


def test_land_profile_without_elevation_has_no_range():
    from app.profile import land_profile

    facts = land_profile({"elevation_min_m": None, "elevation_max_m": 500.0})
    assert facts == []
