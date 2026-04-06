import io
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def write_json_sharded(
    path: Path,
    data: Dict[str, Any],
    list_key: str,
    *,
    max_bytes: Optional[int] = None
) -> List[str]:
    """
    Similar to write_json, but if 'data' exceeds 'max_bytes', it splits
    'data[list_key]' (which must be a list) into multiple shards.
    
    The first shard is written to 'path', subsequent shards to 'stem.part1.suffix', etc.
    Returns the list of filenames created (relative to path.parent).
    """
    cap = _DEFAULT_MAX_BYTES if max_bytes is None else int(max_bytes)
    payload = _to_bytes(data)
    if len(payload) <= cap:
        write_json(path, data, max_bytes=cap)
        return [path.name]

    # Must split.
    items = data.get(list_key)
    if not isinstance(items, list):
        # Fall back to standard error if we can't split
        write_json(path, data, max_bytes=cap)
        return [path.name]

    # Estimation: total_payload / len(items)
    # We'll use a conservative approach: split into N parts.
    # N = ceil(len(payload) / cap * 1.2) to be safe.
    num_shards = math.ceil(len(payload) / cap * 1.2)
    chunk_size = math.ceil(len(items) / num_shards)
    
    filenames = []
    header = {k: v for k, v in data.items() if k != list_key}
    
    for i in range(num_shards):
        start = i * chunk_size
        end = start + chunk_size
        chunk = items[start:end]
        if not chunk and i > 0:
            break
            
        shard_data = dict(header)
        shard_data[list_key] = chunk
        
        # Determine filename
        if i == 0:
            target = path
        else:
            target = path.parent / f"{path.stem}.part{i}{path.suffix}"
            
        try:
            write_json(target, shard_data, max_bytes=cap)
        except ValueError:
            # If still too big, try smaller chunks (halve it)
            # For simplicity, we'll just fail for now, but this is less likely.
            raise
            
        filenames.append(target.name)
        
    return filenames
