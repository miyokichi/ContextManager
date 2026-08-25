"""Shared data models used across the Context Manager pipeline.

Kept deliberately small for the MVP - Glossary / Relation / Revision etc. can
be added later without touching the modules that import from here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ScanStatus(str, Enum):
    NEW = "NEW"
    UPDATED = "UPDATED"
    MOVED = "MOVED"
    DELETED = "DELETED"
    UNCHANGED = "UNCHANGED"


# ---- Scanner -----------------------------------------------------------------


@dataclass
class ScannedFile:
    """One file as seen by the Scanner during a filesystem walk."""

    path: str
    filename: str
    extension: str
    size: int
    modified_at: float
    hash: str
    parent_folders: list[str] = field(default_factory=list)

    @property
    def folder_path(self) -> str:
        return "/".join(self.parent_folders)


@dataclass
class ScanDiffEntry:
    """Result of comparing one ScannedFile against the previous Catalog state."""

    status: ScanStatus
    file: Optional[ScannedFile] = None
    old_path: Optional[str] = None


# ---- Extractor -----------------------------------------------------------------


@dataclass
class ExtractedSection:
    """A bounded, LLM-friendly excerpt from one location in a file
    (a sheet, a slide, a heading section, a PDF page, ...)."""

    location: str
    text: str


@dataclass
class ExtractedDocument:
    kind: str
    sections: list[ExtractedSection] = field(default_factory=list)
    note: str = ""


# ---- AI Context Analyzer -----------------------------------------------------------------


@dataclass
class AnalysisResult:
    summary: str
    locations: list[dict]  # [{"location": str, "summary": str}, ...]


# ---- Catalog -----------------------------------------------------------------


@dataclass
class Resource:
    id: int
    path: str
    filename: str
    folder_path: str
    type: str
    size: int
    modified_at: float
    hash: str
    summary: Optional[str]
    status: str
    indexed_at: Optional[float]


@dataclass
class ResourceLocation:
    id: int
    resource_id: int
    location: str
    summary: Optional[str]


# ---- Search -----------------------------------------------------------------


@dataclass
class SearchResult:
    resource: str
    summary: Optional[str]
    location: Optional[str]
    location_summary: Optional[str]
    score: float
