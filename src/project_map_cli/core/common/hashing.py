# utils/digest_tool_v2/common/hashing.py
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _json_canonical(obj: Any) -> str:
    """
    Canonical JSON string:
      - sort_keys=True
      - minimal separators
      - ensure_ascii=False
      - default=str (so dataclasses, Paths, Enums stringify deterministically)
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def hash_bytes(data: bytes) -> str:
    """
    sha256 over raw bytes → 'sha256:<hex>'.
    """
    h = hashlib.sha256()
    h.update(data)
    return f"sha256:{h.hexdigest()}"


def stable_hash(obj: Any) -> str:
    """
    Deterministic sha256 of a Python object via canonical JSON encoding.
    """
    s = _json_canonical(obj)
    return hash_bytes(s.encode("utf-8"))


def hash_file(path: Path) -> str:
    """
    sha256 of a file's bytes. Streams in chunks to avoid RAM blowups.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"
