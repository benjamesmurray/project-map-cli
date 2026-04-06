# utils/digest_tool_v3/cli.py
from __future__ import annotations

import argparse
import os
import re
import runpy
import sys
import traceback
from pathlib import Path
from typing import Iterable

from .config import Config
from .orchestrator import run as orchestrate


def _positive_int(name: str, value: str) -> int:
    try:
        iv = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from None
    if iv <= 0:
        raise argparse.ArgumentTypeError(f"{name} must be > 0")
    return iv


def _compile_ns_allow(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise argparse.ArgumentTypeError(f"--ns-allow invalid regex: {exc}") from None


def _merge_set(base: Iterable[str] | None, extra: Iterable[str] | None) -> list[str]:
    out = set(base or [])
    out.update(extra or [])
    return sorted(out)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    # If invoked via 'project-map build', adjust the prog name.
    prog_name = "project-map build" if sys.argv[0].endswith("project-map") else "python -m project_map_cli.core"
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description=(
            "Generate compact, deterministic, shardable JSON summaries of a code repo. "
            "Emits v4 shards."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--root", "-r", type=str, required=False, default=".",
                        help="Path to the repository root to analyze (default: current directory).")
    parser.add_argument("--out-dir", "-o", type=str, required=False, default=".project-map/docs/repo_summary/latest",
                        help="Directory to contain the timestamped output subfolder (default: .project-map/docs/repo_summary/latest).")
    parser.add_argument("--ns-allow", type=str, required=False, default=None,
                        help=r"Regex for namespace allow-list (e.g. '^my_pkg(\.|$)'). If omitted, auto-infer from repo.")
    parser.add_argument("--ns-auto", dest="ns_auto", action=argparse.BooleanOptionalAction, default=True,
                        help="Auto-infer namespace allow-list from top-level Python packages when --ns-allow is not provided.")
    parser.add_argument("--timestamped-out", dest="timestamped_out", action=argparse.BooleanOptionalAction, default=True,
                        help="Write shards into a timestamped subdirectory under --out-dir (recommended).")
    parser.add_argument("--bundle-all", dest="bundle_all", action=argparse.BooleanOptionalAction, default=False,
                        help="Also emit a single-file bundle (all.json) next to the shards.")
    parser.add_argument("--bundle-gzip", dest="bundle_gzip", action=argparse.BooleanOptionalAction, default=False,
                        help="When bundling, write all.json.gz (gzip) instead of all.json.")
    parser.add_argument(
        "--db-url-env",
        type=str,
        default="PG_DSN",
        help="Environment variable name containing the Postgres DSN to reflect (default: PG_DSN).",
    )
    parser.add_argument(
        "--profile",
        type=str,
        choices=("full", "light"),
        default="full",
        help="full = all shards; light = skip files_index and ctor.items.",
    )

    # Debug / diagnostics (optional; does not change default behavior)
    parser.add_argument(
        "--traceback",
        dest="traceback",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Print full traceback on failure (also enabled by env DIGEST_V3_TRACEBACK=1).",
    )

    # Exclusion controls
    parser.add_argument("--exclude-spec", type=str, default=None,
                        help="Path to a Python file defining EXCLUDE_DIRS, EXCLUDE_FILES_EXACT, EXCLUDE_GLOBS (sets or lists).")
    parser.add_argument("--exclude-dir", action="append", default=[],
                        help="Directory name to exclude wherever it appears (can be repeated).")
    parser.add_argument("--exclude-file", action="append", default=[],
                        help="Repo-relative file path to exclude exactly (can be repeated).")
    parser.add_argument("--exclude-glob", action="append", default=[],
                        help="Glob/prefix to exclude (can be repeated). Example: '**/*.env*' or 'frontend/public'.")

    # Limits / caps
    parser.add_argument("--max-callsites", type=lambda s: _positive_int("--max-callsites", s), default=5,
                        help="Maximum callsites per symbol in ctor items.")
    parser.add_argument("--max-hotspots", type=lambda s: _positive_int("--max-hotspots", s), default=10,
                        help="Maximum hotspots reported in digest.top.json.")
    parser.add_argument("--max-entry-points", type=lambda s: _positive_int("--max-entry-points", s), default=10,
                        help="Maximum entry points reported in digest.top.json.")
    parser.add_argument("--max-top-symbols", type=lambda s: _positive_int("--max-top-symbols", s), default=10,
                        help="Maximum top symbols reported in ctor.top.json.")
    parser.add_argument("--max-shard-mb", type=lambda s: _positive_int("--max-shard-mb", s), default=10,
                        help="Size cap in megabytes per shard; oversize shards are split or truncated.")

    args = parser.parse_args(argv)

    # Normalize paths
    args.root = Path(args.root).resolve()
    args.out_dir = Path(args.out_dir).resolve()
    if not args.root.exists() or not args.root.is_dir():
        parser.error(f"--root must be an existing directory: {args.root}")
    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover
        parser.error(f"Failed to create --out-dir {args.out_dir}: {exc}")

    # Namespace handling
    if args.ns_allow:
        _compile_ns_allow(args.ns_allow)
    else:
        if not args.ns_auto:
            parser.error("--ns-allow is required when --no-ns-auto is specified")

    # Load exclude spec if provided; merge into lists
    excl_dirs: list[str] = list(args.exclude_dir or [])
    excl_files: list[str] = list(args.exclude_file or [])
    excl_globs: list[str] = list(args.exclude_glob or [])
    if args.exclude_spec:
        spec_path = Path(args.exclude_spec).resolve()
        if not spec_path.exists():
            parser.error(f"--exclude-spec file not found: {spec_path}")
        ns = runpy.run_path(str(spec_path), init_globals={})
        for key, target in (
            ("EXCLUDE_DIRS", excl_dirs),
            ("EXCLUDE_FILES_EXACT", excl_files),
            ("EXCLUDE_GLOBS", excl_globs),
        ):
            if key in ns and ns[key] is not None:
                try:
                    target.extend(list(ns[key]))
                except Exception:
                    parser.error(f"--exclude-spec {spec_path} has invalid {key}; expected iterable of strings")

    # De-dup and sort
    args.exclude_dir = _merge_set([], excl_dirs)
    args.exclude_file = _merge_set([], excl_files)
    args.exclude_glob = _merge_set([], excl_globs)

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    cfg = Config(
        root=args.root,
        out_dir=args.out_dir,
        ns_allow=args.ns_allow,           # may be None; orchestrator will auto-infer if ns_auto=True
        ns_auto=bool(args.ns_auto),
        timestamped_out=bool(args.timestamped_out),
        bundle_all=bool(args.bundle_all),
        bundle_gzip=bool(args.bundle_gzip),
        db_url_env=str(args.db_url_env),
        profile=str(args.profile),
        exclude_dirs=tuple(args.exclude_dir),
        exclude_files_exact=tuple(args.exclude_file),
        exclude_globs=tuple(args.exclude_glob),
        max_callsites=args.max_callsites,
        max_hotspots=args.max_hotspots,
        max_entry_points=args.max_entry_points,
        max_top_symbols=args.max_top_symbols,
        max_shard_mb=args.max_shard_mb,
    )

    try:
        orchestrate(cfg)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        want_tb = bool(getattr(args, "traceback", False)) or os.getenv("DIGEST_V3_TRACEBACK", "").strip() in {"1", "true", "TRUE", "yes", "YES"}
        if want_tb:
            traceback.print_exc(file=sys.stderr)
        else:
            sys.stderr.write(f"[digest_tool_v3] ERROR: {exc}\n")
        # Return 0 to indicate the tool finished its attempt at a scan, 
        # allowing for partial results even if some analyzers failed.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
