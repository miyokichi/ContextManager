"""Search: the Agent-facing entry point for finding candidate resources.

Deliberately thin - all the actual indexing lives in Catalog. This module
exists so the Agent-facing surface (search_context) has a stable name and
signature independent of how the Catalog implements matching underneath
(FTS5 today, embeddings later if ever needed).
"""
from __future__ import annotations

from .catalog import Catalog
from .models import SearchResult


def search_context(catalog: Catalog, query: str, limit: int = 10) -> list[SearchResult]:
    """Search filename / folder path / resource summary / location summary
    and return ranked candidates for the Agent to read further."""
    return catalog.search(query, limit=limit)
