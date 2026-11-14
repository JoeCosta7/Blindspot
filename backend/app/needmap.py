"""The map colored by a need: one Census need for every US county.

Built from the all-county Census call that peers.py also uses, and scored with
the same ratio as the needs in a report, so a county's color on the map agrees
with what its report flags. Only the Census needs: the others are not measured
for every county up front.
"""

from __future__ import annotations

from app.config import Settings
from app.models import NeedMapLayer, NeedMapResponse
from app.needs import ACS_RULES, acs_ratio
from app.sources.census import AcsMetrics, Values, metrics_from_values


def build_need_map(
    all_values: dict[str, Values], national: AcsMetrics, settings: Settings
) -> NeedMapResponse:
    metrics = {fips: metrics_from_values(values) for fips, values in all_values.items()}
    layers: list[NeedMapLayer] = []
    for rule in ACS_RULES:
        baseline = getattr(national, rule.metric)
        counties: dict[str, tuple[float, float]] = {}
        for fips, county in metrics.items():
            value = getattr(county, rule.metric)
            ratio = acs_ratio(rule, value, baseline)
            if ratio is not None:
                counties[fips] = (round(float(value), 4), round(ratio, 3))
        if not counties:  # no national figure, or no county has one
            continue
        layers.append(
            NeedMapLayer(
                id=rule.id,  # type: ignore[arg-type]
                label=rule.label,
                theme=rule.theme,  # type: ignore[arg-type]
                unit=rule.unit,  # type: ignore[arg-type]
                national_value=float(baseline),
                lower_is_worse=rule.lower_is_worse,
                counties=counties,
            )
        )
    return NeedMapResponse(
        layers=layers,
        ratio_breaks=[
            1.0,
            settings.need_ratio_elevated,
            settings.need_ratio_high,
            1.0 + settings.need_severity_span,
        ],
        source=settings.acs_label,
    )
