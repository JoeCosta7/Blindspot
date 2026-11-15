"""Curated project documents (grant packets, guides, property forms).

data/project_documents.json is written by hand. Gemini may name additional
forms to gather locally, but it never supplies URLs; those come only from
this table or from a funding lead's source_url.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.models import DocumentKind, FundingMatch, ProjectDocument


class DocumentEntry(BaseModel):
    id: str
    name: str
    kind: DocumentKind
    description: str
    agency: str | None = None
    source_url: str | None = None
    eligible_solutions: list[str]
    eligible_asset_types: list[str] | None
    verified_on: str | None = None


@lru_cache(maxsize=4)
def load_documents(path: Path) -> tuple[DocumentEntry, ...]:
    return tuple(
        DocumentEntry.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))
    )


def match_documents(
    documents: tuple[DocumentEntry, ...] | list[DocumentEntry],
    solution_id: str | None,
    asset_type: str | None,
) -> list[ProjectDocument]:
    """A document matches when the solution is eligible and, if the document
    restricts asset types, the host's type is one of them. Untyped (own-idea)
    opportunities get no curated documents."""
    if not solution_id:
        return []
    matched: list[DocumentEntry] = []
    for doc in documents:
        if solution_id not in doc.eligible_solutions:
            continue
        if doc.eligible_asset_types is not None and asset_type not in doc.eligible_asset_types:
            continue
        matched.append(doc)
    matched.sort(key=lambda d: (d.source_url is None, d.name))
    return [
        ProjectDocument(
            id=doc.id,
            name=doc.name,
            kind=doc.kind,
            description=doc.description,
            agency=doc.agency,
            source_url=doc.source_url,
            verified_on=doc.verified_on,
        )
        for doc in matched
    ]


def documents_from_funding(funding: list[FundingMatch]) -> list[ProjectDocument]:
    """Turn each funding lead that has a URL into an application / program
    document, so the Documents list includes the grant packet itself."""
    out: list[ProjectDocument] = []
    seen: set[str] = set()
    for lead in funding:
        if not lead.source_url or lead.source_url in seen:
            continue
        seen.add(lead.source_url)
        kind = "grant_application" if lead.program_id.startswith("grantsgov:") else "program_guide"
        out.append(
            ProjectDocument(
                id=f"funding:{lead.program_id}",
                name=f"{lead.name}: program materials",
                kind=kind,
                description=(
                    lead.key_terms or "Application materials or program page for this funding lead."
                ),
                agency=lead.agency,
                source_url=lead.source_url,
                verified_on=lead.verified_on,
            )
        )
    return out


def merge_documents(*groups: list[ProjectDocument]) -> list[ProjectDocument]:
    """Dedupe by source_url when present, else by id. Prefer entries that
    already have a URL."""
    by_key: dict[str, ProjectDocument] = {}
    for group in groups:
        for doc in group:
            key = doc.source_url or f"id:{doc.id}"
            existing = by_key.get(key)
            if existing is None or (not existing.source_url and doc.source_url):
                by_key[key] = doc
    return sorted(
        by_key.values(),
        key=lambda d: (d.source_url is None, d.name),
    )
