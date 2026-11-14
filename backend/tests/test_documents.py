"""Curated project documents: match by solution/asset, merge with funding URLs."""

from app import documents
from app.config import BACKEND_DIR
from app.models import FundingMatch

DATA = BACKEND_DIR / "data"
DOCS = documents.load_documents(DATA / "project_documents.json")


def test_project_documents_json_loads():
    assert len(DOCS) >= 5
    assert any(d.id == "epa_brownfields_marc_packet" for d in DOCS)
    assert any(d.source_url is None for d in DOCS)  # local-only forms


def test_match_community_solar_on_brownfield():
    matched = documents.match_documents(DOCS, "community_solar", "brownfield")
    ids = {d.id for d in matched}
    assert "epa_brownfields_marc_packet" in ids
    assert "epa_repowering_screening" in ids
    assert "site_access_agreement" in ids
    assert "utility_interconnection_request" in ids
    # Landfill-only packet must not appear on a brownfield.
    assert "epa_lmop_landfill_gas" not in ids


def test_match_skips_wrong_asset_type():
    matched = documents.match_documents(DOCS, "community_solar", "library")
    ids = {d.id for d in matched}
    assert "epa_brownfields_marc_packet" not in ids
    assert "epa_repowering_screening" not in ids
    # Forms with no asset-type restriction still match.
    assert "site_access_agreement" in ids
    assert "irs_elective_pay_overview" in ids


def test_match_returns_nothing_without_solution():
    assert documents.match_documents(DOCS, None, "brownfield") == []


def test_documents_from_funding_uses_source_url():
    leads = [
        FundingMatch(
            program_id="epa_brownfields_fy27",
            name="EPA Brownfields",
            agency="EPA",
            status="open",
            deadline="2027-01-21",
            key_terms="Cleanup grants capped at $500,000.",
            verified_on="2026-11-13",
            source_url="https://www.epa.gov/brownfields/example",
        ),
        FundingMatch(
            program_id="no_url",
            name="No URL",
            agency="X",
            status="uncertain",
            deadline=None,
            key_terms="",
            verified_on=None,
            source_url=None,
        ),
    ]
    docs = documents.documents_from_funding(leads)
    assert len(docs) == 1
    assert docs[0].id == "funding:epa_brownfields_fy27"
    assert docs[0].source_url == "https://www.epa.gov/brownfields/example"
    assert docs[0].kind == "program_guide"


def test_merge_prefers_url_and_dedupes():
    a = documents.match_documents(DOCS, "landfill_gas_energy", "lmop_landfill")
    b = documents.documents_from_funding(
        [
            FundingMatch(
                program_id="x",
                name="X",
                agency="EPA",
                status="open",
                deadline=None,
                key_terms="",
                verified_on=None,
                source_url="https://www.epa.gov/lmop/lmop-landfill-and-project-database",
            )
        ]
    )
    merged = documents.merge_documents(a, b)
    urls = [d.source_url for d in merged if d.source_url]
    assert len(urls) == len(set(urls))
    assert any(d.id == "epa_lmop_landfill_gas" for d in merged)
