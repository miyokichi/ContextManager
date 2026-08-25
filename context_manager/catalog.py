"""Catalog: SQLite-backed store of Resources and ResourceLocations.

MVP scope on purpose: two tables plus two FTS5 indexes for search. No
knowledge graph, no Glossary/Relation/Revision - those can be added as new
tables later without disturbing this module's public methods.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .models import Resource, ResourceLocation, ScanDiffEntry, ScanStatus, SearchResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS resource (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    folder_path TEXT NOT NULL,
    type TEXT,
    size INTEGER,
    modified_at REAL,
    hash TEXT,
    summary TEXT,
    status TEXT NOT NULL,
    indexed_at REAL
);

CREATE TABLE IF NOT EXISTS resource_location (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL REFERENCES resource(id) ON DELETE CASCADE,
    location TEXT NOT NULL,
    summary TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS resource_fts USING fts5(
    filename, folder_path, summary, tokenize='unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS location_fts USING fts5(
    location, summary, resource_id UNINDEXED, tokenize='unicode61'
);
"""

_FTS_TERM_RE = re.compile(r"\S+")


@dataclass
class _PreviousEntry:
    path: str
    hash: str
    id: int


class Catalog:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ---- scan/diff bookkeeping ------------------------------------------------

    def previous_state(self) -> dict[str, _PreviousEntry]:
        """Everything the Scanner needs to diff a fresh walk against - excludes
        rows already marked DELETED, so a re-created file at the same path or
        hash reads as NEW/MOVED rather than UNCHANGED."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, path, hash FROM resource WHERE status != ?",
                (ScanStatus.DELETED.value,),
            ).fetchall()
        finally:
            conn.close()
        return {r["path"]: _PreviousEntry(path=r["path"], hash=r["hash"] or "", id=r["id"]) for r in rows}

    def apply_scan_entry(self, entry: ScanDiffEntry) -> int | None:
        """Persist one Scanner diff entry into the resource table.

        Returns the resource id (None for DELETED). Does NOT touch
        summary/locations - call set_analysis() separately for entries that
        need (re-)analysis (NEW/UPDATED/MOVED; UNCHANGED is skipped by design).
        """
        conn = self._connect()
        try:
            if entry.status == ScanStatus.DELETED:
                conn.execute(
                    "UPDATE resource SET status = ? WHERE path = ?",
                    (ScanStatus.DELETED.value, entry.old_path),
                )
                conn.commit()
                return None

            f = entry.file
            folder_path = f.folder_path

            if entry.status == ScanStatus.MOVED:
                conn.execute(
                    """UPDATE resource
                       SET path = ?, filename = ?, folder_path = ?, type = ?, size = ?,
                           modified_at = ?, hash = ?, status = ?
                       WHERE path = ?""",
                    (
                        f.path, f.filename, folder_path, f.extension, f.size,
                        f.modified_at, f.hash, entry.status.value, entry.old_path,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO resource
                           (path, filename, folder_path, type, size, modified_at, hash, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET
                           filename=excluded.filename, folder_path=excluded.folder_path,
                           type=excluded.type, size=excluded.size, modified_at=excluded.modified_at,
                           hash=excluded.hash, status=excluded.status""",
                    (f.path, f.filename, folder_path, f.extension, f.size, f.modified_at, f.hash, entry.status.value),
                )

            row = conn.execute("SELECT id FROM resource WHERE path = ?", (f.path,)).fetchone()
            resource_id = row["id"]
            self._refresh_resource_fts(conn, resource_id)
            conn.commit()
            return resource_id
        finally:
            conn.close()

    # ---- analysis (AI Context Analyzer output) ---------------------------------

    def set_analysis(
        self, resource_id: int, summary: str, locations: list[dict], indexed_at: float | None = None
    ) -> None:
        indexed_at = time.time() if indexed_at is None else indexed_at
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE resource SET summary = ?, indexed_at = ? WHERE id = ?",
                (summary, indexed_at, resource_id),
            )
            conn.execute("DELETE FROM resource_location WHERE resource_id = ?", (resource_id,))
            conn.execute("DELETE FROM location_fts WHERE resource_id = ?", (resource_id,))
            for loc in locations:
                location = loc.get("location", "")
                loc_summary = loc.get("summary", "")
                cur = conn.execute(
                    "INSERT INTO resource_location (resource_id, location, summary) VALUES (?, ?, ?)",
                    (resource_id, location, loc_summary),
                )
                conn.execute(
                    "INSERT INTO location_fts (rowid, location, summary, resource_id) VALUES (?, ?, ?, ?)",
                    (cur.lastrowid, location, loc_summary, resource_id),
                )
            self._refresh_resource_fts(conn, resource_id)
            conn.commit()
        finally:
            conn.close()

    def _refresh_resource_fts(self, conn: sqlite3.Connection, resource_id: int) -> None:
        row = conn.execute(
            "SELECT filename, folder_path, summary FROM resource WHERE id = ?", (resource_id,)
        ).fetchone()
        if row is None:
            return
        conn.execute("DELETE FROM resource_fts WHERE rowid = ?", (resource_id,))
        conn.execute(
            "INSERT INTO resource_fts (rowid, filename, folder_path, summary) VALUES (?, ?, ?, ?)",
            (resource_id, row["filename"], row["folder_path"], row["summary"] or ""),
        )

    # ---- reads -----------------------------------------------------------------

    def resources_needing_index(self) -> list[Resource]:
        """NEW/UPDATED/MOVED always need (re-)analysis. UNCHANGED is normally
        skipped (per the MVP rule), but not when indexed_at is still NULL -
        that means a previous scan's extraction/analysis never completed for
        this file (crash, transient API error, ...), so it must be retried
        rather than silently left unindexed forever."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM resource WHERE status != ? AND (status IN (?, ?, ?) OR indexed_at IS NULL)",
                (
                    ScanStatus.DELETED.value,
                    ScanStatus.NEW.value, ScanStatus.UPDATED.value, ScanStatus.MOVED.value,
                ),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_resource(r) for r in rows]

    def get_resource_by_path(self, path: str) -> Resource | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM resource WHERE path = ?", (path,)).fetchone()
        finally:
            conn.close()
        return self._row_to_resource(row) if row else None

    def get_resource_by_id(self, resource_id: int) -> Resource | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM resource WHERE id = ?", (resource_id,)).fetchone()
        finally:
            conn.close()
        return self._row_to_resource(row) if row else None

    def list_resources(self, include_deleted: bool = False) -> list[Resource]:
        conn = self._connect()
        try:
            if include_deleted:
                rows = conn.execute("SELECT * FROM resource ORDER BY path").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM resource WHERE status != ? ORDER BY path", (ScanStatus.DELETED.value,)
                ).fetchall()
        finally:
            conn.close()
        return [self._row_to_resource(r) for r in rows]

    def list_locations(self, resource_id: int) -> list[ResourceLocation]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM resource_location WHERE resource_id = ?", (resource_id,)
            ).fetchall()
        finally:
            conn.close()
        return [
            ResourceLocation(id=r["id"], resource_id=r["resource_id"], location=r["location"], summary=r["summary"])
            for r in rows
        ]

    def count_by_status(self) -> dict[str, int]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT status, COUNT(*) AS c FROM resource GROUP BY status").fetchall()
        finally:
            conn.close()
        return {r["status"]: r["c"] for r in rows}

    def _row_to_resource(self, row: sqlite3.Row) -> Resource:
        return Resource(
            id=row["id"], path=row["path"], filename=row["filename"], folder_path=row["folder_path"],
            type=row["type"], size=row["size"], modified_at=row["modified_at"], hash=row["hash"],
            summary=row["summary"], status=row["status"], indexed_at=row["indexed_at"],
        )

    # ---- search ------------------------------------------------------------------

    def _fts_query(self, query: str) -> str | None:
        terms = _FTS_TERM_RE.findall(query.strip())
        if not terms:
            return None
        parts = [f'"{t.replace(chr(34), chr(34) * 2)}"*' for t in terms]
        return " OR ".join(parts)

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Search filename, folder path, resource summary, and location
        summary. MVP: SQLite FTS5 (bm25), no embeddings required."""
        fts_query = self._fts_query(query)
        if not fts_query:
            return []

        conn = self._connect()
        try:
            candidates = []
            for row in conn.execute(
                """SELECT r.id AS resource_id, r.path AS path, r.summary AS resource_summary,
                          NULL AS location, NULL AS location_summary, bm25(resource_fts) AS score
                   FROM resource_fts
                   JOIN resource r ON r.id = resource_fts.rowid
                   WHERE resource_fts MATCH ? AND r.status != ?
                   ORDER BY score LIMIT ?""",
                (fts_query, ScanStatus.DELETED.value, limit * 3),
            ):
                candidates.append(dict(row))

            for row in conn.execute(
                """SELECT r.id AS resource_id, r.path AS path, r.summary AS resource_summary,
                          lf.location AS location, lf.summary AS location_summary, bm25(location_fts) AS score
                   FROM location_fts lf
                   JOIN resource_location rl ON rl.id = lf.rowid
                   JOIN resource r ON r.id = rl.resource_id
                   WHERE location_fts MATCH ? AND r.status != ?
                   ORDER BY score LIMIT ?""",
                (fts_query, ScanStatus.DELETED.value, limit * 3),
            ):
                candidates.append(dict(row))
        finally:
            conn.close()

        if not candidates:
            return []

        scores = [c["score"] for c in candidates]
        lo, hi = min(scores), max(scores)
        span = (hi - lo) or 1.0

        def normalize(s: float) -> float:
            # bm25(): more negative == more relevant
            return round(1.0 - ((s - lo) / span), 4)

        candidates.sort(key=lambda c: c["score"])
        seen: set[tuple] = set()
        results: list[SearchResult] = []
        for c in candidates:
            key = (c["resource_id"], c["location"])
            if key in seen:
                continue
            seen.add(key)
            results.append(
                SearchResult(
                    resource=c["path"],
                    summary=c["resource_summary"],
                    location=c["location"],
                    location_summary=c["location_summary"],
                    score=normalize(c["score"]),
                )
            )
            if len(results) >= limit:
                break
        return results
