"""Command-line interface for Context Manager.

    uv run main.py registry add <folder>
    uv run main.py registry remove <folder>
    uv run main.py registry list
    uv run main.py scan [--no-index] [--no-llm] [--llm-backend {anthropic,openai_compat,heuristic}]
    uv run main.py search "<query>" [--limit N] [--json]
    uv run main.py structure <path>
    uv run main.py read <path> [--location <name>] [--max-chars N]
    uv run main.py sheets <path.xlsx>
    uv run main.py range <path.xlsx> <sheet> <range>
    uv run main.py formula <path.xlsx> <sheet> <cell>
    uv run main.py stats

Global options (--config, --db) go before the subcommand.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import reader, registry, scanner
from .analyzer.llm_analyzer import get_analyzer
from .catalog import Catalog
from .extractors import extract_file
from .extractors.base import ExtractionError
from .search import search_context

DEFAULT_DB_PATH = "catalog.sqlite3"
DEFAULT_CONFIG_PATH = "config.yaml"


def cmd_registry_add(args):
    config = registry.add_root(args.folder, args.config)
    print(f"registered: {args.folder}")
    print("current roots:")
    for r in config.roots:
        print(f"  - {r}")


def cmd_registry_remove(args):
    config = registry.remove_root(args.folder, args.config)
    print(f"removed: {args.folder}")
    print("current roots:")
    for r in config.roots:
        print(f"  - {r}")


def cmd_registry_list(args):
    config = registry.load_config(args.config)
    if not config.roots:
        print("(no roots registered yet - use `registry add <folder>`)")
        return
    for r in config.roots:
        print(r)


def cmd_scan(args):
    config = registry.load_config(args.config)
    if not config.roots:
        print("no roots registered - use `registry add <folder>` first", file=sys.stderr)
        sys.exit(1)

    catalog = Catalog(args.db)
    print(f"scanning {len(config.roots)} root(s)...")
    current = scanner.scan_roots(config.roots, config.all_excludes())
    print(f"found {len(current)} file(s)")

    previous = catalog.previous_state()
    diff = scanner.diff_scan(previous, current)

    counts: dict[str, int] = {}
    for entry in diff:
        counts[entry.status.value] = counts.get(entry.status.value, 0) + 1
        catalog.apply_scan_entry(entry)

    print("diff:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "(no changes)")

    if args.no_index:
        return

    to_index = catalog.resources_needing_index()
    if not to_index:
        print("nothing to (re-)index")
        return

    analyzer = get_analyzer(force_heuristic=args.no_llm, backend=args.llm_backend)
    print(f"indexing {len(to_index)} resource(s) using {type(analyzer).__name__}...")
    for i, resource in enumerate(to_index, start=1):
        print(f"  [{i}/{len(to_index)}] {resource.path}")
        try:
            extracted = extract_file(resource.path)
        except ExtractionError as e:
            print(f"    skip (extraction failed): {e}")
            continue
        try:
            parent_folders = resource.folder_path.split("/") if resource.folder_path else []
            result = analyzer.analyze(resource.path, parent_folders, extracted)
        except Exception as e:
            print(f"    skip (analysis failed): {e}")
            continue
        catalog.set_analysis(resource.id, result.summary, result.locations, time.time())

    print("done.")


def cmd_search(args):
    catalog = Catalog(args.db)
    results = search_context(catalog, args.query, limit=args.limit)
    if not results:
        print("(no matches)")
        return
    if args.json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
        return
    for r in results:
        print(f"[{r.score:.2f}] {r.resource}")
        if r.location:
            print(f"    location: {r.location} - {r.location_summary}")
        else:
            print(f"    summary: {r.summary}")


def cmd_structure(args):
    print(json.dumps(reader.list_resource_structure(args.path), ensure_ascii=False, indent=2))


def cmd_read(args):
    print(reader.read_resource(args.path, location=args.location, max_chars=args.max_chars))


def cmd_sheets(args):
    for name in reader.list_sheets(args.path):
        print(name)


def cmd_range(args):
    for row in reader.read_range(args.path, args.sheet, args.range):
        print(row)


def cmd_formula(args):
    formula = reader.read_formula(args.path, args.sheet, args.cell)
    print(formula if formula is not None else "(not a formula)")


def cmd_stats(args):
    catalog = Catalog(args.db)
    counts = catalog.count_by_status()
    if not counts:
        print("(catalog is empty - run `scan` first)")
        return
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-manager", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="registry config YAML path")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="catalog SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_reg = sub.add_parser("registry", help="manage registered folders")
    reg_sub = p_reg.add_subparsers(dest="registry_command", required=True)

    p_reg_add = reg_sub.add_parser("add", help="register a folder")
    p_reg_add.add_argument("folder")
    p_reg_add.set_defaults(func=cmd_registry_add)

    p_reg_remove = reg_sub.add_parser("remove", help="unregister a folder")
    p_reg_remove.add_argument("folder")
    p_reg_remove.set_defaults(func=cmd_registry_remove)

    p_reg_list = reg_sub.add_parser("list", help="list registered folders")
    p_reg_list.set_defaults(func=cmd_registry_list)

    p_scan = sub.add_parser("scan", help="scan registered folders, diff, extract + analyze new/changed files")
    p_scan.add_argument("--no-index", action="store_true", help="only update the diff, skip extraction/analysis")
    p_scan.add_argument("--no-llm", action="store_true", help="force the offline heuristic analyzer (no API calls)")
    p_scan.add_argument(
        "--llm-backend",
        choices=["anthropic", "openai_compat", "heuristic"],
        default=None,
        help="which analyzer backend to use (default: auto-detect from env vars - "
        "see CONTEXT_MANAGER_ANALYZER / CONTEXT_MANAGER_OPENAI_BASE_URL)",
    )
    p_scan.set_defaults(func=cmd_scan)

    p_search = sub.add_parser("search", help="search the catalog")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--json", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_structure = sub.add_parser("structure", help="list a resource's locations (sheets/slides/sections/pages)")
    p_structure.add_argument("path")
    p_structure.set_defaults(func=cmd_structure)

    p_read = sub.add_parser("read", help="read a resource, optionally scoped to one location")
    p_read.add_argument("path")
    p_read.add_argument("--location", default=None)
    p_read.add_argument("--max-chars", type=int, default=12000)
    p_read.set_defaults(func=cmd_read)

    p_sheets = sub.add_parser("sheets", help="list sheet names in an Excel workbook")
    p_sheets.add_argument("path")
    p_sheets.set_defaults(func=cmd_sheets)

    p_range = sub.add_parser("range", help="read a cell range from an Excel sheet")
    p_range.add_argument("path")
    p_range.add_argument("sheet")
    p_range.add_argument("range")
    p_range.set_defaults(func=cmd_range)

    p_formula = sub.add_parser("formula", help="read a cell's formula from an Excel sheet")
    p_formula.add_argument("path")
    p_formula.add_argument("sheet")
    p_formula.add_argument("cell")
    p_formula.set_defaults(func=cmd_formula)

    p_stats = sub.add_parser("stats", help="show catalog status counts")
    p_stats.set_defaults(func=cmd_stats)

    return parser


def _force_utf8_streams() -> None:
    """Windows' default console/redirect encoding is the system codepage, not
    UTF-8 - without this, Japanese folder/file names and summaries get
    silently mangled on print or when output is redirected to a file."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv=None):
    _force_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
