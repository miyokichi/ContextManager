"""Scanner: walk registered folders and diff against the previous Catalog state.

Only decides NEW / UPDATED / MOVED / DELETED / UNCHANGED - it never touches
the Catalog itself and never reads file *content* beyond hashing bytes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from .models import ScanDiffEntry, ScannedFile, ScanStatus
from .registry import is_excluded


class PreviousEntry(Protocol):
    """Minimal shape diff_scan() needs from the Catalog's previous state."""

    path: str
    hash: str


def compute_hash(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def scan_root(root: str, exclude_patterns: list[str]) -> list[ScannedFile]:
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"registered root does not exist: {root}")

    results: list[ScannedFile] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        current_dir = Path(dirpath)

        kept_dirs = []
        for d in dirnames:
            rel = (current_dir / d).relative_to(root_path).as_posix()
            if is_excluded(rel, d, exclude_patterns):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fn in filenames:
            full = current_dir / fn
            rel = full.relative_to(root_path).as_posix()
            if is_excluded(rel, fn, exclude_patterns):
                continue
            try:
                stat = full.stat()
                file_hash = compute_hash(full)
            except OSError:
                # unreadable (permissions, in-use lock, race with deletion) - skip
                continue

            parent_folders = list(full.relative_to(root_path).parent.parts)
            results.append(
                ScannedFile(
                    path=str(full.resolve()),
                    filename=fn,
                    extension=full.suffix.lower().lstrip("."),
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    hash=file_hash,
                    parent_folders=parent_folders,
                )
            )
    return results


def scan_roots(roots: Iterable[str], exclude_patterns: list[str]) -> list[ScannedFile]:
    all_results: list[ScannedFile] = []
    for root in roots:
        all_results.extend(scan_root(root, exclude_patterns))
    return all_results


def diff_scan(
    previous: Mapping[str, PreviousEntry], current: list[ScannedFile]
) -> list[ScanDiffEntry]:
    """Compare a fresh scan against the previous Catalog state (path -> entry
    with .path/.hash, e.g. Catalog.previous_state())."""

    previous_by_hash: dict[str, list[PreviousEntry]] = {}
    for prev in previous.values():
        previous_by_hash.setdefault(prev.hash, []).append(prev)

    current_paths = {f.path for f in current}
    matched_prev_paths: set[str] = set()
    entries: list[ScanDiffEntry] = []

    for f in current:
        prev = previous.get(f.path)
        if prev is not None:
            matched_prev_paths.add(prev.path)
            status = ScanStatus.UNCHANGED if prev.hash == f.hash else ScanStatus.UPDATED
            entries.append(ScanDiffEntry(status=status, file=f))
            continue

        # Not at a previously-known path - see if it's a file that moved:
        # same hash, previously at a path that no longer exists on disk.
        moved_from = None
        for candidate in previous_by_hash.get(f.hash, []):
            if candidate.path in current_paths or candidate.path in matched_prev_paths:
                continue
            moved_from = candidate
            break

        if moved_from is not None:
            matched_prev_paths.add(moved_from.path)
            entries.append(ScanDiffEntry(status=ScanStatus.MOVED, file=f, old_path=moved_from.path))
        else:
            entries.append(ScanDiffEntry(status=ScanStatus.NEW, file=f))

    for path, prev in previous.items():
        if path in matched_prev_paths or path in current_paths:
            continue
        entries.append(ScanDiffEntry(status=ScanStatus.DELETED, file=None, old_path=path))

    return entries
