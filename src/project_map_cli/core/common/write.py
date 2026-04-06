# utils/digest_tool_v2/common/write.py
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

# Default cap aligns with v2 spec (2 MB). Orchestrator can override per-call.
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024


def _to_bytes(data: Any) -> bytes:
    """
    Deterministic JSON serialization:
      - UTF-8 (no BOM)
      - sorted keys
      - minimal separators to reduce size
      - ensure_ascii=False so paths remain readable
    """
    # Use StringIO to avoid double-encoding work when measuring size.
    s = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return s.encode("utf-8")


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """
    Atomic write to `target`:
      - Write to a temporary file in the same directory
      - fsync file and directory
      - Rename into place
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    # Create temp in same dir to ensure rename is atomic on the same filesystem.
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=str(target.parent), prefix=target.name + ".", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())

    # Fsync the directory entry after rename to reduce crash windows.
    os.replace(tmp_path, target)
    dir_fd = os.open(str(target.parent), os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_json(path: Path, data: Any, *, max_bytes: Optional[int] = None) -> None:
    """
    Serialize `data` to deterministic JSON and write atomically to `path`.
    Enforces a size cap (defaults to 2 MB) and raises ValueError on overflow.

    Args:
        path: Destination path (will be created/overwritten).
        data: JSON-serializable Python object.
        max_bytes: Optional cap in bytes. If None, uses the module default.
    """
    cap = _DEFAULT_MAX_BYTES if max_bytes is None else int(max_bytes)
    if cap <= 0:
        raise ValueError(f"max_bytes must be > 0 (got {cap})")

    payload = _to_bytes(data)
    size = len(payload)
    if size > cap:
        human = f"{size/1024/1024:.3f}MB"
        cap_h = f"{cap/1024/1024:.3f}MB"
        # Don’t silently split here; splitting policies are analyzer-specific.
        raise ValueError(
            f"Shard too large for {path.name}: {human} exceeds cap {cap_h}. "
            "Analyzer must split or truncate before writing."
        )

    _atomic_write_bytes(path, payload)
