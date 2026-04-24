# utils/digest_tool_v3/analyzers/imports_repo_only.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..config import Config
from ..common import ast_utils, filters


def _src_module(path: Path, cfg: Config) -> str:
    """Best-effort dotted module name for a file path."""
    return ast_utils.module_name_from_path(path, cfg.root)


def _dst_module(import_rec: Dict[str, Any], src_mod: str) -> str:
    """
    Best-effort destination module name from a compressed import record.
    - For 'imp': use top-level 'mod' captured by ast_utils (already top segment).
    - For 'from': prefer explicit module; if relative/empty, fall back to src top-level.
    """
    kind = import_rec.get("k")
    mod = import_rec.get("mod", "") or ""
    if kind == "imp":
        # E.g. `import pkg.sub as sub` → mod='pkg.sub'
        return mod
    elif kind == "from":
        if mod:
            # E.g. `from pkg.sub import Thing` → mod='pkg.sub'
            return mod
        # Relative import like `from . import x` → use src top-level
        return src_mod.split(".", 1)[0] if src_mod else ""
    else:
        return ""


def analyze(cfg: Config, py_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Build a filtered import graph where both endpoints are inside the repo namespace.

    Output:
      {
        "edges": [
          {"src": "pkg.mod", "dst": "pkg.other", "pid": 123, "ln": 45},
          ...
        ],
        "dropped_edges": 27
      }
    """
    edges: List[Dict[str, Any]] = []
    dropped = 0

    for path in py_files:
        pid = pid_by_path.get(path)
        if pid is None:
            # Shouldn't happen if orchestrator assigned PIDs for all scanned files.
            continue

        src_mod = _src_module(path, cfg)
        src_ok = filters.is_repo_module(src_mod, cfg.ns_allow_re)

        # Walk this file's imports
        for rec in ast_utils.list_imports(path):
            ln = int(rec.get("ln", 0) or 0)
            dst_mod = _dst_module(rec, src_mod)

            # Decide inclusion: both endpoints must be repo modules
            if src_ok and filters.is_repo_module(dst_mod, cfg.ns_allow_re):
                edges.append({
                    "src": src_mod,
                    "dst": dst_mod,
                    "pid": pid,
                    "ln": ln,
                })
            else:
                dropped += 1

    # Deterministic sort
    edges.sort(key=lambda e: (e["src"], e["dst"], e["pid"], e["ln"]))

    return {
        "edges": edges,
        "dropped_edges": dropped,
    }
