"""Folder Registry: which folders are watched, and what's excluded.

This is the only place a human interacts with directly - registering a
folder is the entire setup step. Everything downstream (scanning, indexing,
search) is automatic.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Always excluded, regardless of user config - Office lock files, VCS dirs,
# common scratch folders.
DEFAULT_EXCLUDES = [
    "**/~$*",
    "**/.git/**",
    "**/temp/**",
    "**/$RECYCLE.BIN/**",
    "**/desktop.ini",
]

DEFAULT_CONFIG_PATH = "config.yaml"


@dataclass
class RegistryConfig:
    roots: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def all_excludes(self) -> list[str]:
        merged = []
        for pattern in DEFAULT_EXCLUDES + self.exclude:
            if pattern not in merged:
                merged.append(pattern)
        return merged


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> RegistryConfig:
    path = Path(path)
    if not path.exists():
        return RegistryConfig()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    roots = [r["path"] if isinstance(r, dict) else r for r in (data.get("roots") or [])]
    exclude = list(data.get("exclude") or [])
    return RegistryConfig(roots=roots, exclude=exclude)


def save_config(config: RegistryConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    path = Path(path)
    data = {
        "roots": [{"path": r} for r in config.roots],
        "exclude": config.exclude,
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def add_root(root_path: str, config_path: str | Path = DEFAULT_CONFIG_PATH) -> RegistryConfig:
    config = load_config(config_path)
    normalized = str(Path(root_path).resolve())
    if normalized not in config.roots:
        config.roots.append(normalized)
    save_config(config, config_path)
    return config


def remove_root(root_path: str, config_path: str | Path = DEFAULT_CONFIG_PATH) -> RegistryConfig:
    config = load_config(config_path)
    normalized = str(Path(root_path).resolve())
    config.roots = [r for r in config.roots if r != normalized]
    save_config(config, config_path)
    return config


def is_excluded(rel_posix_path: str, filename: str, patterns: list[str]) -> bool:
    """`rel_posix_path` is the path relative to the registered root, with
    forward slashes. Checked against both the full relative path and the
    bare filename so patterns like "**/~$*" and "desktop.ini" both work."""
    for pattern in patterns:
        if fnmatch.fnmatch(rel_posix_path, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
    return False
