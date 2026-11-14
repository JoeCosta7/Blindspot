"""Highlights pick the factors where the county beats its benchmark most."""

from app import highlights
from app.models import Factor, ProfileFact
from tests.conftest import make_asset, make_metrics


def factor(factor_id, value="x", comparison="US: y"):
    return Factor(
        id=factor_id,
        category="workforce",
        label=factor_id,
        value=value,
        comparison=comparison,
        source="test",
    )


def fact(fact_id, value, national):
    return ProfileFact(
        id=fact_id,
        category="workforce",
        label=fact_id,
        value=value,
        national_value=national,
        unit="share",
        source="test",
    )


def test_best_three_by_ratio_in_the_right_direction(settings):
    found = highlights.find_highlights(
        factors=[
            factor(i)
            for i in (
                "trades_workers",
                "no_broadband",
                "bachelors_or_higher",
                "electric_heat",
                "sites_mine_land",
                "construction_jobs",
            )
        ],
        profile=[
            fact("trades_workers", 0.15, 0.10),  # 1.5x
            fact("no_broadband", 0.03, 0.09),  # lower is better: 3.0x
            fact("bachelors_or_higher", 0.20, 0.35),  # worse than the US: dropped
            fact("construction_jobs", 0.11, 0.10),  # 1.1x: below the cut-off
        ],
        acs=make_metrics(electric_heat_share=0.60),
        acs_national=make_metrics(electric_heat_share=0.40),  # 1.5x
        energy=None,
        ee_metrics=None,
        farm_row=None,
        assets=[make_asset("m1", "mine_land", acres=5000.0)],
        settings=settings,
        averages=lambda data_dir: {"sites_mine_land": 1000.0},  # 5.0x
    )
    assert [(h.factor_id, h.times_better) for h in found] == [
        ("sites_mine_land", 5.0),
        ("no_broadband", 3.0),
        ("trades_workers", 1.5),
    ]
    assert found[0].benchmark == "Average US county: 1,000 acres"
    assert found[1].benchmark == "US: y"
