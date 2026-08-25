"""CSV / plain-text / Markdown extractor - read as-is, bounded by size."""
from __future__ import annotations

import csv
from pathlib import Path

from ..models import ExtractedDocument, ExtractedSection
from .base import ExtractionError


def extract(path, max_chars: int = 4000, max_csv_rows: int = 20) -> ExtractedDocument:
    p = Path(path)
    ext = p.suffix.lower().lstrip(".")
    try:
        if ext == "csv":
            with open(p, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
                rows = []
                for i, row in enumerate(csv.reader(f)):
                    if i >= max_csv_rows:
                        rows.append("...(truncated)")
                        break
                    rows.append(", ".join(row))
                text = "\n".join(rows)
        else:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(max_chars + 1)
                if len(text) > max_chars:
                    text = text[:max_chars] + "...(truncated)"
    except OSError as e:
        raise ExtractionError(f"failed to read file: {e}") from e

    return ExtractedDocument(kind=ext or "text", sections=[ExtractedSection(location="content", text=text)])
