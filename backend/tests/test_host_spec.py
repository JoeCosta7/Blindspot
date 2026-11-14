"""The per-opportunity host spec: only listed values survive; no key -> catalogue."""

import asyncio

from app.scout import host_spec


def asked(**kw):
    base = dict(
        looking_for="large paved parking lots at shopping centres",
        sources=[
            "search_imagery",
            "search_public_buildings",
            "search_made_up",
            "search_properties",
        ],
        report_site_types=["brownfield", "volcano"],
        public_kinds=[],
        property_types=["Retail", "Spaceport"],
        imagery_text="large paved parking lot with few trees",
        judge_as="land",
        check_ids=["paved_area", "no_such_check"],
    )
    base.update(kw)
    return host_spec._AskedSpec(**base)


def test_only_listed_values_survive(settings):
    spec = host_spec._clean(asked(), settings)
    # public buildings dropped: no kinds asked for; unknown source dropped.
    assert spec.source_order == ("search_imagery", "search_properties")
    assert spec.property_types == ("Retail",) and spec.site_types == ("brownfield",)
    assert spec.check_ids == ("paved_area",) and spec.land and spec.chosen_by == "gemini"
    assert host_spec._clean(asked(sources=["search_made_up"]), settings) is None


def test_no_key_uses_the_catalogue(settings):
    spec = asyncio.run(
        host_spec.resolve(
            "Energy-bill assistance sign-up drive",
            None,
            [],
            "utility_assistance_drive",
            ["library", "school"],
            settings,
        )
    )
    assert spec.chosen_by == "catalogue" and spec.public_kinds == ("library", "school")


def test_the_opportunitys_own_words_come_first(settings):
    spec = asyncio.run(
        host_spec.resolve(
            "Solar-and-battery resilience hub at the library",
            None,
            [],
            "resilience_hub",
            ["school", "library", "college"],
            settings,
        )
    )
    assert spec.public_kinds[0] == "library" and spec.source_order[0] == "search_public_buildings"
    other = asyncio.run(
        host_spec.resolve("Tool library in an empty warehouse", None, [], None, [], settings)
    )
    assert "library" in other.public_kinds and other.property_types[0] == "Industrial"
