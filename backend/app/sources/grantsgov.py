"""Grants.gov search2: live federal funding listings, shown as unchecked leads.

`POST https://api.grants.gov/v1/api/search2` needs no key. Request fields
`keyword`, `oppStatuses` (pipe-delimited) and `rows` were confirmed with live
calls on 2026-11-14. The live hits differ from the documentation: the agency
name is `agency` (docs: `agencyName`) and the ALN list is `cfdaList` (docs:
`alnist`), so both spellings are read. Dates are MM/DD/YYYY; `closeDate` can be
"". Titles carry HTML entities.

The search is full-text and noisy ("solar" finds DoD HIV/AIDS programs), and
the eligibility filter drops EPA's FY 2027 Brownfields grants, so nothing is
filtered by eligibility. Instead each hand-written query in
data/grants_queries.json keeps only hits from listed agencies whose titles
contain one of its terms. Results are leads, not matches: the UI says so.

Federal listings are national, so a search is cached by keyword, not county.
"""

from __future__ import annotations

import html
import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from app.cache import HOUR, cached_fetch
from app.config import Settings
from app.models import FundingMatch
from app.sources import SourceError

URL = "https://api.grants.gov/v1/api/search2"
# Human-facing page for one opportunity; returned HTTP 200 on 2026-11-14.
DETAIL_URL = "https://www.grants.gov/search-results-detail/{id}"
SOURCE_LABEL = "Grants.gov"
STATUSES = "forecasted|posted"
ROWS = 100
TTL_S = 12 * HOUR


class GrantQuery(BaseModel):
    """One hand-written search, and the project types its results are shown for."""

    keyword: str
    solution_ids: list[str]
    # A hit is kept only if its agencyCode starts with one of these.
    agency_prefixes: list[str]
    # ...its title contains one of these (case ignored)...
    title_terms: list[str]
    # ...and none of these.
    exclude_terms: list[str] = []
    # When set, only for opportunities whose main site is one of these types.
    asset_types: list[str] | None = None
    note: str | None = None

    def applies_to(self, solution_id: str, asset_type: str | None) -> bool:
        return solution_id in self.solution_ids and (
            self.asset_types is None or asset_type in self.asset_types
        )


@lru_cache(maxsize=4)
def load_queries(path: Path) -> tuple[GrantQuery, ...]:
    return tuple(GrantQuery.model_validate(item) for item in json.loads(path.read_text()))


def _iso(value: Any) -> str | None:
    """MM/DD/YYYY -> YYYY-MM-DD. Empty or unreadable -> None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parse_hit(hit: dict[str, Any]) -> dict[str, Any] | None:
    """One search hit trimmed to what a lead shows. None without an id or title."""
    opp_id = str(hit.get("id") or "").strip()
    title = html.unescape(str(hit.get("title") or "")).strip()
    if not opp_id or not title:
        return None
    return {
        "id": opp_id,
        "number": str(hit.get("number") or ""),
        "title": title,
        "agency_code": str(hit.get("agencyCode") or ""),
        "agency": html.unescape(str(hit.get("agency") or hit.get("agencyName") or "")),
        "status": str(hit.get("oppStatus") or "").lower(),
        "open_date": _iso(hit.get("openDate")),
        "close_date": _iso(hit.get("closeDate")),
        "aln": list(hit.get("cfdaList") or hit.get("alnist") or []),
    }


async def search(
    client: httpx.AsyncClient, settings: Settings, keyword: str
) -> tuple[dict[str, Any], bool]:
    """Posted and forecasted listings for a keyword:
    {"fetched_on": ISO date, "hits": [parsed hit, ...]}."""

    async def fetch() -> dict[str, Any]:
        try:
            resp = await client.post(
                URL,
                json={"keyword": keyword, "oppStatuses": STATUSES, "rows": ROWS},
                timeout=settings.source_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"Grants.gov request failed: {type(exc).__name__}") from exc
        if resp.status_code == 429:
            raise SourceError("Grants.gov rate limit reached")
        if resp.status_code != 200:
            raise SourceError(f"Grants.gov returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SourceError("Grants.gov returned a response that is not JSON") from exc
        if body.get("errorcode") != 0:
            raise SourceError(f"Grants.gov error: {body.get('msg') or body.get('errorcode')}")
        hits = (body.get("data") or {}).get("oppHits") or []
        parsed = [p for p in (parse_hit(h) for h in hits) if p is not None]
        return {"fetched_on": date.today().isoformat(), "hits": parsed}

    return await cached_fetch(
        settings.cache_dir,
        "grantsgov_search",
        {"keyword": keyword, "statuses": STATUSES, "rows": ROWS},
        TTL_S,
        fetch,
    )


def _keep(query: GrantQuery, hit: dict[str, Any], today: str) -> bool:
    title = hit["title"].lower()
    return (
        hit["status"] in ("posted", "forecasted")
        and any(hit["agency_code"].startswith(p) for p in query.agency_prefixes)
        and any(t.lower() in title for t in query.title_terms)
        and not any(t.lower() in title for t in query.exclude_terms)
        # A cached result can outlive a deadline.
        and (hit["close_date"] is None or hit["close_date"] >= today)
    )


def leads_for(
    queries: tuple[GrantQuery, ...] | list[GrantQuery],
    results: dict[str, dict[str, Any]],
    solution_id: str,
    asset_type: str | None,
    limit: int,
    today: str | None = None,
) -> list[FundingMatch]:
    """Live listings for one opportunity, as funding leads. A posted listing is
    `open` with its close date as the deadline; a forecasted one is `uncertain`
    (not yet taking applications). Searches that failed are simply absent."""
    today = today or date.today().isoformat()
    hits: dict[str, tuple[dict[str, Any], str]] = {}
    for query in queries:
        result = results.get(query.keyword)
        if result is None or not query.applies_to(solution_id, asset_type):
            continue
        for hit in result["hits"]:
            if hit["id"] not in hits and _keep(query, hit, today):
                hits[hit["id"]] = (hit, result["fetched_on"])
    ordered = sorted(
        hits.values(),
        key=lambda item: (item[0]["status"] != "posted", item[0]["close_date"] or "9999"),
    )
    return [
        FundingMatch(
            program_id=f"grantsgov:{hit['id']}",
            name=hit["title"],
            agency=hit["agency"] or hit["agency_code"],
            status="open" if hit["status"] == "posted" else "uncertain",
            deadline=hit["close_date"],
            key_terms=(
                f"Grants.gov listing {hit['number']} ({hit['status']}), found by keyword "
                "search. Eligibility and fit not checked by us; read the notice."
            ),
            verified_on=fetched_on,
            source_url=DETAIL_URL.format(id=hit["id"]),
            kind="grant",
            county_eligible="unknown",
        )
        for hit, fetched_on in ordered[:limit]
    ]


def keywords_for(
    queries: tuple[GrantQuery, ...] | list[GrantQuery],
    wanted: list[tuple[str, str | None]],
) -> list[str]:
    """Distinct keywords to search for these (solution_id, asset_type) pairs."""
    keywords: list[str] = []
    for query in queries:
        if query.keyword not in keywords and any(query.applies_to(s, a) for s, a in wanted):
            keywords.append(query.keyword)
    return keywords
