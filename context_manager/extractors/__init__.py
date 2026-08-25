"""Extractor dispatch: bounded, LLM-friendly peeks into original files.

These are indexing-time peeks (small, capped size) - never the whole
document. For deep, on-demand reads once a candidate location is known, see
`context_manager.reader` instead.
"""
from __future__ import annotations

from pathlib import Path

from ..models import ExtractedDocument
from . import excel, pdf, ppt, text as text_extractor, word
from .base import ExtractionError

_DISPATCH = {
    "xlsx": excel.extract,
    "xlsm": excel.extract,
    "pptx": ppt.extract,
    "pdf": pdf.extract,
    "docx": word.extract,
    "csv": text_extractor.extract,
    "txt": text_extractor.extract,
    "md": text_extractor.extract,
}


def supported_extensions() -> set[str]:
    return set(_DISPATCH.keys())


def extract_file(path) -> ExtractedDocument:
    p = Path(path)
    ext = p.suffix.lower().lstrip(".")
    fn = _DISPATCH.get(ext)
    if fn is None:
        raise ExtractionError(f"unsupported extension: .{ext}")
    return fn(p)


__all__ = ["extract_file", "supported_extensions", "ExtractionError"]
