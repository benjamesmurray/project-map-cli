# utils/digest_tool_v3/analyzers/files_index.py
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Any

from ..config import Config
from ..common import ast_utils


def _top_level_pkg(path: Path, root: Path) -> str:
    """
    Determine shard key (top-level package/folder) for a file.
    Files directly under root go into '__root__'.
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except Exception:
        return "__root__"
    parts = rel.parts
    if len(parts) <= 1:
        return "__root__"
    return parts[0]


def _count_loc_nonempty(text: str) -> int:
    """Non-empty line count (whitespace-only lines are ignored)."""
    return sum(1 for ln in text.splitlines() if ln.strip())


def _file_record(cfg: Config, path: Path, pid: int) -> Dict[str, Any]:
    """
    Build compressed file record per spec:
      - p: pid
      - l: non-empty LOC
      - d: module docstring flag 0/1
      - i: imports [{k,mod,n,ln}]
      - c: classes  [{name,ln,doc}]
      - f: functions [{name,ln,doc}]
      - m: methods   [{class,name,ln,doc}]
    """
    # Fast read once for LOC
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = ""
    loc = _count_loc_nonempty(text)

    # Structured bits via AST helpers
    has_doc = 1 if ast_utils.get_module_docstring(path) else 0
    imports = ast_utils.list_imports(path)
    classes, functions, methods = ast_utils.list_defs(path)

    # Deterministic ordering already enforced in ast_utils; keep as-is
    return {
        "p": pid,
        "l": loc,
        "d": has_doc,
        "i": imports,
        "c": classes,
        "f": functions,
        "m": methods,
    }


def analyze(cfg: Config, py_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Produce sharded files_index docs and lightweight stats.

    Returns:
      {
        "shards": { "<pkg>": { "files": [ {p,l,d,i,c,f,m}, ... ] } , ... },
        "stats":  { "py_file_count": int, "class_count": int, "function_count": int, "method_count": int }
      }
    """
    shards: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    class_count = 0
    function_count = 0
    method_count = 0

    # Deterministic iteration by repo-relative path (already sorted upstream)
    for path in py_files:
        pid = pid_by_path.get(path)
        if pid is None:
            # If a .py wasn't PID-mapped, skip; invariants in orchestrator should prevent this.
            continue

        rec = _file_record(cfg, path, pid)
        class_count += len(rec["c"])
        function_count += len(rec["f"])
        method_count += len(rec["m"])

        shard_key = _top_level_pkg(path, cfg.root)
        shards[shard_key].append(rec)

    # Sort files within each shard by p (pid) for stability
    out_shards: Dict[str, Dict[str, Any]] = {}
    for shard_key, files in shards.items():
        files.sort(key=lambda r: r["p"])
        out_shards[shard_key] = {"files": files}

    stats = {
        "py_file_count": len(py_files),
        "class_count": class_count,
        "function_count": function_count,
        "method_count": method_count,
    }

    return {"shards": out_shards, "stats": stats}
