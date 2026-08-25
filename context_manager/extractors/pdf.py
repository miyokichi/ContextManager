"""PDF extractor: per-page text, bounded to the first N pages."""
from __future__ import annotations

from ..models import ExtractedDocument, ExtractedSection
from .base import ExtractionError


def extract(path, max_pages: int = 30, max_chars_per_page: int = 2000) -> ExtractedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ExtractionError("pypdf is required to read PDF files") from e

    try:
        reader = PdfReader(str(path))
    except Exception as e:
        raise ExtractionError(f"failed to open pdf: {e}") from e

    total = len(reader.pages)
    limit = min(total, max_pages)
    note = f"only the first {max_pages} of {total} pages were extracted" if total > max_pages else ""

    sections = []
    for i in range(limit):
        try:
            text = (reader.pages[i].extract_text() or "").strip()
        except Exception:
            text = ""
        if len(text) > max_chars_per_page:
            text = text[:max_chars_per_page] + "...(truncated)"
        sections.append(ExtractedSection(location=f"Page {i + 1}", text=text))

    return ExtractedDocument(kind="pdf", sections=sections, note=note)
