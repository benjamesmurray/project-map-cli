# utils/digest_tool_v3/analyzers/digest_top.py
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..config import Config
from ..common import ast_utils


def _avg(numer: int, denom: int) -> float:
    if denom <= 0:
        return 0.0
    return round(float(numer) / float(denom), 3)


def _score_entry_point(info: Mapping[str, Any]) -> int:
    """
    Simple entry-point score:
      +2 if has_main_guard
      +1 per argparse call
    """
    score = 0
    if info.get("has_main_guard"):
        score += 2
    score += int(len(info.get("argparse_calls", ())))
    return score


def _find_entry_points(cfg: Config,
                       py_files: List[Path],
                       pid_by_path: Dict[Path, int]) -> List[Dict[str, Any]]:
    """
    Inspect modules for __main__ guards and argparse usage, then rank.
    """
    items: List[Dict[str, Any]] = []

    for path in py_files:
        ep = ast_utils.detect_entry_points(path)
        score = _score_entry_point(ep)
        if score <= 0:
            continue
        pid = pid_by_path.get(path)
        if pid is None:
            # Shouldn't happen when orchestrator supplied consistent mapping
            continue
        module = ast_utils.module_name_from_path(path, cfg.root)
        items.append({
            "pid": pid,
            "module": module,
            "score": score,
            "has_main_guard": bool(ep.get("has_main_guard", False)),
            "argparse_count": len(ep.get("argparse_calls", ())),
        })

    # Sort: score desc, module asc
    items.sort(key=lambda d: (-d["score"], d["module"]))
    return items[: cfg.max_entry_points]


def _compute_hotspots(imports_doc: Mapping[str, Any], max_items: int) -> List[Dict[str, Any]]:
    """
    Compute fan-in/fan-out per module from repo-only edges.
    """
    edges: Iterable[Mapping[str, Any]] = imports_doc.get("edges", []) or []
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()

    for e in edges:
        src = str(e.get("src", ""))
        dst = str(e.get("dst", ""))
        if not src or not dst:
            continue
        fan_out[src] += 1
        fan_in[dst] += 1

    modules = set(fan_in.keys()) | set(fan_out.keys())
    rows: List[Dict[str, Any]] = []
    for m in modules:
        out_c = fan_out.get(m, 0)
        in_c = fan_in.get(m, 0)
        score = in_c + out_c
        rows.append({"module": m, "in": int(in_c), "out": int(out_c), "score": int(score)})

    rows.sort(key=lambda d: (-d["score"], d["module"]))
    return rows[: max_items]


def analyze(cfg: Config,
            *,
            files_index_stats: Mapping[str, Any],
            imports_doc: Mapping[str, Any],
            py_files: Optional[List[Path]] = None,
            pid_by_path: Optional[Dict[Path, int]] = None) -> Dict[str, Any]:
    """
    Build digest.top.json using previously computed stats and import graph.

    Optional:
      - py_files, pid_by_path: if provided, we will compute entry_points;
        otherwise, entry_points will be an empty list (determinism preserved).
    """
    py_count = int(files_index_stats.get("py_file_count", 0) or 0)
    class_count = int(files_index_stats.get("class_count", 0) or 0)
    func_count = int(files_index_stats.get("function_count", 0) or 0)
    method_count = int(files_index_stats.get("method_count", 0) or 0)

    digest: Dict[str, Any] = {
        "py_file_count": py_count,
        "classes": class_count,
        "functions": func_count,
        "methods": method_count,
        "avg_funcs_per_file": _avg(func_count, py_count),
    }

    # Hotspots from import graph (always available)
    digest["hotspots"] = _compute_hotspots(imports_doc, cfg.max_hotspots)

    # Entry points only if inputs provided; else empty by design
    if py_files is not None and pid_by_path is not None:
        digest["entry_points"] = _find_entry_points(cfg, py_files, pid_by_path)
    else:
        digest["entry_points"] = []

    return digest
