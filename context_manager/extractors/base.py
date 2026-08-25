"""Shared types/errors for extractors."""
from __future__ import annotations

from ..models import ExtractedDocument, ExtractedSection

__all__ = ["ExtractedDocument", "ExtractedSection", "ExtractionError"]


class ExtractionError(Exception):
    """Raised when a file cannot be turned into an ExtractedDocument
    (unsupported/corrupt file, missing optional dependency, ...)."""
