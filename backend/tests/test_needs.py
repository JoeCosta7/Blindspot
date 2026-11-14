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


# --- stretch needs --------------------------------------------------------------


def energy(bill=142.0, co2=767.0, **state):
    return {
        "state_abbr": "ZZ",
        "state": {
            "residential_monthly_bill_usd": bill,
            "residential_price_cents_kwh": 15.0,
            "grid_co2_lb_per_mwh": co2,
            **state,
        },
        "national": {
            "residential_monthly_bill_usd": 142.0,
            "residential_price_cents_kwh": 16.5,
            "grid_co2_lb_per_mwh": 767.0,
        },
        "electricity_year": 2024,
        "grid_year": 2023,
    }


def test_electricity_cost_need_uses_the_state_bill(settings):
    assert needs.electricity_cost_need(None, settings) is None
    assert needs.electricity_cost_need(energy(bill=150.0), settings) is None  # ratio 1.06

    need = needs.electricity_cost_need(energy(bill=177.5), settings)  # ratio 1.25
    assert (need.id, need.level, need.severity) == ("high_electricity_cost", "high", 50)
    assert (need.value, need.national_value, need.unit) == (177.5, 142.0, "usd_per_month")
    assert "State average for ZZ" in need.detail and "2024" in need.source

    no_bill = energy(residential_monthly_bill_usd=None)
    assert needs.electricity_cost_need(no_bill, settings) is None


def test_grid_carbon_need_uses_the_state_rate(settings):
    assert needs.grid_carbon_need(energy(co2=800.0), settings) is None
    need = needs.grid_carbon_need(energy(co2=1954.4), settings)
    assert (need.id, need.level, need.severity) == ("carbon_intensive_grid", "high", 100)
    assert need.unit == "lb_co2_per_mwh" and "eGRID2023" in need.source
    # Puerto Rico has an eGRID rate but no EIA bill: only this need can fire.
    assert needs.grid_carbon_need(energy(co2=None), settings) is None


def farm(dairies=0, hogs=0, digesters=0, building=0):
    return {
        "dairy_farms_500_plus": dairies,
        "hog_farms_1000_plus": hogs,
        "digesters_operational": digesters,
        "digesters_under_construction": building,
    }


def test_farm_waste_need_levels(settings):
    assert needs.farm_waste_need(None, settings) is None
    assert needs.farm_waste_need(farm(), settings) is None
    assert needs.farm_waste_need(farm(dairies=2, digesters=2), settings) is None

    high = needs.farm_waste_need(farm(dairies=22, hogs=3), settings)
    assert (high.level, high.severity, high.value) == ("high", 75, 25.0)
    assert "22 dairy farms with 500 or more cows" in high.detail
    assert "3 hog farms with 1,000 or more hogs" in high.detail
    assert "no operating manure digester" in high.detail

    # EPA's hog threshold is 2,000 head; the census only has a 1,000 class.
    hogs_only = needs.farm_waste_need(farm(hogs=1), settings)
    assert (hogs_only.level, hogs_only.severity) == ("elevated", 40)
    assert "1 hog farm with" in hogs_only.detail and "no 2,000-hog class" in hogs_only.detail

    some = needs.farm_waste_need(farm(dairies=5, digesters=2, building=1), settings)
    assert some.level == "elevated"
    assert "2 operating digesters" in some.detail and "1 under construction" in some.detail


def ee_metrics(heat=9.0, canopy=0.05, built_up=0.02):
    return {
        "heat_island_c": heat,
        "lst_built_up_c": 38.0,
        "lst_natural_c": 29.0,
        "built_up_share": built_up,
        "canopy_built_up": canopy,
    }


def test_urban_heat_need_levels(settings):
    assert needs.urban_heat_need(None, settings) is None
    assert needs.urban_heat_need(ee_metrics(heat=6.4), settings) is None  # below elevated
    assert needs.urban_heat_need(ee_metrics(heat=None), settings) is None
    elevated = needs.urban_heat_need(ee_metrics(heat=6.5), settings)
    assert (elevated.level, elevated.severity) == ("elevated", 48)  # (6.5-5.1)/(8.0-5.1)
    high = needs.urban_heat_need(ee_metrics(heat=9.0), settings)
    assert (high.id, high.level, high.severity, high.unit) == ("urban_heat", "high", 100, "celsius")
    assert (high.value, high.national_value) == (9.0, 5.1)


def test_low_tree_canopy_need_is_scored_like_an_acs_need(settings):
    assert needs.low_tree_canopy_need(ee_metrics(canopy=0.14), settings) is None
    assert needs.low_tree_canopy_need(ee_metrics(canopy=None), settings) is None
    elevated = needs.low_tree_canopy_need(ee_metrics(canopy=0.12), settings)  # 0.133/0.12 = 1.11
    assert (elevated.id, elevated.level) == ("low_tree_canopy", "elevated")
    assert needs.low_tree_canopy_need(ee_metrics(canopy=0.0), settings).severity == 100
    assert needs.low_tree_canopy_need(ee_metrics(canopy=0.05), settings).level == "high"


def test_earth_engine_needs_skip_counties_with_almost_no_built_up_land(settings):
    sparse = ee_metrics(heat=9.0, canopy=0.0, built_up=0.0002)
    assert needs.urban_heat_need(sparse, settings) is None
    assert needs.low_tree_canopy_need(sparse, settings) is None
