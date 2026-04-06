# utils/digest_tool_v3/analyzers/fe_calls.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Config

# Methods we care about
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}

# Cap per file (spec: 100)
_MAX_CALLS_PER_FILE = 100

# ------- tiny helpers for parsing JS/Vue fragments deterministically -------

_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)

# axios.<method>(url, config?)
_AXIOS_CALL_RE = re.compile(
    r"\baxios\.(?P<m>get|post|put|patch|delete|options|head)\s*\(\s*(?P<url>[^,)\n]+)"
    r"(?:\s*,\s*(?P<cfg>\{.*?\}))?\s*\)",
    re.IGNORECASE | re.DOTALL,
)

# fetch(url, options?)
_FETCH_CALL_RE = re.compile(
    r"\bfetch\s*\(\s*(?P<url>[^,)\n]+)"
    r"(?:\s*,\s*(?P<opts>\{.*?\}))?\s*\)",
    re.IGNORECASE | re.DOTALL,
)

# Pull top-level object keys from a JS object literal (very simple, non-nested)
# Matches keys like: ident:, "ident":, 'ident': (no computed keys)
_OBJ_KEY_RE = re.compile(
    r"""
    (?P<key>
      [A-Za-z_][A-Za-z0-9_]*         # identifier
      |"(?:[^"\\]|\\.)*?"            # "string"
      |'(?:[^'\\]|\\.)*?'            # 'string'
    )\s*:
    """,
    re.VERBOSE,
)


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (len(s) >= 2) and ((s[0] == s[-1]) and s[0] in ('"', "'")):
        return s[1:-1]
    return s


def _normalize_template(s: str) -> str:
    """
    Normalize JS template/backtick strings:
    - Remove surrounding backticks if present
    - Replace ${...} with {var}
    """
    s = s.strip()
    if s.startswith("`") and s.endswith("`"):
        s = s[1:-1]
    # Replace ${...} segments with {var}
    return re.sub(r"\$\{.*?\}", "{var}", s)


def _normalize_url_token(tok: str) -> Optional[str]:
    """
    Given a URL token (literal string or template literal), normalize to plain text.
    Returns None if it's clearly non-literal (starts with identifier or call).
    """
    t = tok.strip()
    # Heuristic: if it starts with a quote or backtick, treat as literal/template.
    if t.startswith(("'", '"', "`")):
        if t[0] in ("'", '"'):
            return _strip_quotes(t)
        return _normalize_template(t)
    # Allow trivial concatenations like "/api/" + x (normalize only the literal part)
    m = re.match(r"^((['\"]).*?\2)\s*\+\s*.+$", t)
    if m:
        return _strip_quotes(m.group(1)) + "{var}"
    # Otherwise, non-literal → skip to keep shard compact and deterministic
    return None


def _extract_top_level_keys(obj_literal: str) -> List[str]:
    """
    Extract top-level keys from a JS object literal string.
    This is intentionally shallow and conservative.
    """
    keys: List[str] = []
    for m in _OBJ_KEY_RE.finditer(obj_literal):
        raw = m.group("key")
        if raw.startswith(("'", '"')):
            raw = _strip_quotes(raw)
        keys.append(raw)
    # Deduplicate while preserving order
    seen = set()
    out: List[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _extract_method_from_opts(opts_literal: str, default_method: str = "GET") -> str:
    """
    For fetch second-arg object, find method: 'POST'|'GET'|...
    """
    # Very simple search for method: 'X' or "X"
    m = re.search(r"\bmethod\s*:\s*(['\"])(?P<m>[A-Za-z]+)\1", opts_literal, re.IGNORECASE)
    if m:
        val = m.group("m").upper()
        if val in _HTTP_METHODS:
            return val
    return default_method


def _component_name(path: Path) -> str:
    return path.name


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _iter_code_blocks(path: Path, raw: str) -> List[str]:
    """
    For .vue → return all <script> blocks.
    For .ts/.tsx/.js/.jsx → return the whole file as one block.
    """
    if path.suffix.lower() == ".vue":
        return [m.group("body") or "" for m in _SCRIPT_RE.finditer(raw)]
    return [raw]


def analyze(cfg: Config, fe_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Emit a single shard:
      {
        "calls": [
          {"url": "/api/scene", "method": "GET", "pid": 301, "component": "SceneView.vue", "params": ["symbol","tf"]},
          ...
        ]
      }
    """
    results: List[Dict[str, Any]] = []

    for path in fe_files:
        pid = pid_by_path.get(path)
        if pid is None:
            continue

        text = _read_text(path)
        if not text:
            continue

        calls_in_file: List[Dict[str, Any]] = []

        # Iterate code blocks according to file type
        for body in _iter_code_blocks(path, text):
            body_compact = body

            # axios.<method>(url, config?)
            for ax in _AXIOS_CALL_RE.finditer(body_compact):
                method = ax.group("m").upper()
                raw_url = ax.group("url") or ""
                cfg_obj = ax.group("cfg") or ""

                url = _normalize_url_token(raw_url)
                if not url:
                    continue

                params = _extract_top_level_keys(cfg_obj) if cfg_obj else []
                calls_in_file.append({
                    "url": url,
                    "method": method,
                    "pid": pid,
                    "component": _component_name(path),
                    "params": params,
                })
                if len(calls_in_file) >= _MAX_CALLS_PER_FILE:
                    break

            if len(calls_in_file) >= _MAX_CALLS_PER_FILE:
                break

            # fetch(url, options?)
            for fc in _FETCH_CALL_RE.finditer(body_compact):
                raw_url = fc.group("url") or ""
                opts = fc.group("opts") or ""

                url = _normalize_url_token(raw_url)
                if not url:
                    continue

                method = _extract_method_from_opts(opts, default_method="GET") if opts else "GET"
                params = _extract_top_level_keys(opts) if opts else []

                calls_in_file.append({
                    "url": url,
                    "method": method,
                    "pid": pid,
                    "component": _component_name(path),
                    "params": params,
                })
                if len(calls_in_file) >= _MAX_CALLS_PER_FILE:
                    break

            if len(calls_in_file) >= _MAX_CALLS_PER_FILE:
                break

        # Deterministic per-file ordering
        calls_in_file.sort(key=lambda c: (c["url"], c["method"], c["component"], c["pid"]))
        results.extend(calls_in_file)

    # Global deterministic sort
    results.sort(key=lambda c: (c["url"], c["method"], c["component"], c["pid"]))

    return {"calls": results}
