# utils/digest_tool_v3/common/fs_scan.py
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Dict, List

from ..config import Config


def _rel_posix(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _excluded_by_dirs(rel_parts: tuple[str, ...], exclude_dir_names: tuple[str, ...]) -> bool:
    """
    Exclude if any path component matches one of the names in exclude_dir_names.
    """
    if not exclude_dir_names:
        return False
    excl = set(exclude_dir_names)
    return any(part in excl for part in rel_parts)


def _match_globs(rel: str, patterns: List[str]) -> bool:
    """
    Match repo-relative POSIX path against:
      - glob patterns (contain '*', '?', or '[...]')
      - plain prefixes (like 'frontend/public' → matches 'frontend/public' or subpaths)
    """
    for pat in patterns:
        if any(ch in pat for ch in "*?[]"):
            if fnmatch.fnmatch(rel, pat):
                return True
        else:
            # Treat as prefix match on path boundary
            if rel == pat or rel.startswith(pat.rstrip("/") + "/"):
                return True
    return False


def _should_exclude(root: Path, p: Path, cfg: Config, merged_globs: List[str]) -> bool:
    rel = _rel_posix(root, p)
    rel_parts = p.relative_to(root).parts

    # 1) directory name component exclude
    if _excluded_by_dirs(rel_parts, cfg.exclude_dirs):
        return True

    # 2) exact file path exclude
    if rel in cfg.exclude_files_exact:
        return True

    # 3) glob/prefix exclude (user globs + default globs)
    if _match_globs(rel, merged_globs):
        return True

    return False


def scan(cfg: Config) -> Dict[str, List[Path]]:
    """
    Deterministically walk the repo, filtering via:
      - exclude_dirs (by name, at any depth)
      - exclude_files_exact (repo-relative posix)
      - exclude_globs + DEFAULT_EXCLUDE (glob/prefix)

    Returns sorted lists for each file bucket, including Kotlin/Gradle/Config.
    """
    root = cfg.root
    merged_globs: List[str] = sorted(set(cfg.exclude_globs) | set(cfg.excludes))

    all_files: List[Path] = []
    for p in sorted(root.rglob("*"), key=lambda x: x.as_posix()):
        if not p.is_file():
            continue
        if _should_exclude(root, p, cfg, merged_globs):
            continue
        all_files.append(p)

    def _pick_suffixes(suffixes: tuple[str, ...]) -> List[Path]:
        sufset = set(s.lower() for s in suffixes)
        return [p for p in all_files if p.suffix.lower() in sufset]

    py_files = _pick_suffixes(cfg.py_suffixes)
    vue_files = _pick_suffixes(cfg.vue_suffixes)
    js_ts_files = _pick_suffixes(cfg.js_ts_suffixes)
    sql_files = _pick_suffixes(cfg.sql_suffixes)
    sqlite_files = [p for p in all_files if p.suffix.lower() in set(s.lower() for s in cfg.sqlite_suffixes)]
    fe_files = sorted(set(vue_files + js_ts_files), key=lambda p: p.as_posix())

    # New buckets
    gradle_files: List[Path] = []
    kt_files: List[Path] = []
    go_files: List[Path] = []
    rs_files: List[Path] = []
    config_files: List[Path] = []

    for p in all_files:
        # Gradle files first (so .kts build scripts don't get misclassified as Kotlin symbols)
        if cfg.is_gradle_file(p):
            gradle_files.append(p)
        elif cfg.is_kotlin_file(p):
            kt_files.append(p)
        elif cfg.is_go_file(p):
            go_files.append(p)
        elif cfg.is_rust_file(p):
            rs_files.append(p)

        # Config files (can overlap with gradle.properties; allow overlap intentionally)
        if cfg.is_config_file(p):
            config_files.append(p)

    # De-dup + deterministic ordering
    gradle_files = sorted(set(gradle_files), key=lambda p: p.as_posix())
    kt_files = sorted(set(kt_files), key=lambda p: p.as_posix())
    go_files = sorted(set(go_files), key=lambda p: p.as_posix())
    rs_files = sorted(set(rs_files), key=lambda p: p.as_posix())
    config_files = sorted(set(config_files), key=lambda p: p.as_posix())

    # Deterministic order is preserved by the initial sorted walk
    return {
        "py_files": py_files,
        "vue_files": vue_files,
        "js_ts_files": js_ts_files,
        "ts_files": js_ts_files, # Alias for orchestrator
        "fe_files": fe_files,
        "sql_files": sql_files,
        "sqlite_files": sqlite_files,
        "kt_files": kt_files,
        "go_files": go_files,
        "rs_files": rs_files,
        "gradle_files": gradle_files,
        "config_files": config_files,
        "all_files": all_files,
    }
