# utils/digest_tool_v2/common/pid_registry.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Tuple


def _detect_root(paths: Iterable[Path]) -> Path:
    """
    Infer the common repository root from a set of absolute file paths.
    Falls back to the parent of the first file if only one is present.
    """
    paths = list(paths)
    if not paths:
        # Empty input: no root
        return Path("/")
    if len(paths) == 1:
        return paths[0].anchor and Path(paths[0].anchor) or paths[0].parent
    # pathlib.commonpath returns a string
    common = Path(*Path(paths[0]).parts)
    for p in paths[1:]:
        # Shrink 'common' until it is a prefix of p
        pp = Path(*Path(p).parts)
        while common != pp and not str(pp).startswith(str(common) + str(Path().anchor if Path().anchor else "")):
            # The above check is brittle across OS; use .parts comparison instead
            if len(common.parts) == 0:
                break
            common = Path(*common.parts[:-1])
        # quick exit if we hit the filesystem root
        if len(common.parts) <= 1:
            break
    # If that heuristic produced nonsense, use os.path.commonpath
    try:
        import os
        common_str = os.path.commonpath([str(p) for p in paths])
        return Path(common_str)
    except Exception:
        # Last resort: parent of the first path
        return paths[0].parent


def _rel_posix(path: Path, root: Path) -> str:
    """
    Repo-relative POSIX path (no leading './').
    If 'path' is not under 'root', fall back to the basename to maintain determinism.
    """
    try:
        rel = path.relative_to(root)
        return rel.as_posix()
    except Exception:
        return path.as_posix().lstrip("./").replace("\\", "/")


def assign(all_files: List[Path]) -> Tuple[Dict[Path, int], Dict[int, str]]:
    """
    Assign stable integer PIDs to the provided file list.

    Inputs:
      - all_files: absolute Paths, deduplicated and sorted deterministically upstream.

    Returns:
      - pid_by_path: Dict[Path, int]  (keyed by the original Path objects)
      - paths_map:  Dict[int, str]    (PID → repo-relative POSIX path string)
    """
    pid_by_path: Dict[Path, int] = {}
    paths_map: Dict[int, str] = {}

    if not all_files:
        return pid_by_path, paths_map

    # Detect a common root for relative mapping (fs_scan supplies cfg.root implicitly)
    root = _detect_root(all_files)

    # Sort again by relative path to guard against any upstream ordering drift
    ordered = sorted(all_files, key=lambda p: _rel_posix(p, root))

    for pid, p in enumerate(ordered):
        rel = _rel_posix(p, root)
        pid_by_path[p] = pid
        paths_map[pid] = rel

    return pid_by_path, paths_map
