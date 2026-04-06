# infra/digest_tool_v6/common/tree_sitter_util.py
from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# -------------------------------
# Structured result + error model
# -------------------------------

@dataclass(frozen=True)
class TSError:
    code: str
    message: str
    detail: str = ""

    def to_dict(self) -> Dict[str, str]:
        d = {"code": self.code, "message": self.message}
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass(frozen=True)
class TSResult:
    ok: bool
    value: Any = None
    error: Optional[TSError] = None

    @staticmethod
    def ok_(value: Any) -> "TSResult":
        return TSResult(ok=True, value=value, error=None)

    @staticmethod
    def err(code: str, message: str, detail: str = "") -> "TSResult":
        return TSResult(ok=False, value=None, error=TSError(code=code, message=message, detail=detail))


# -------------------------------
# Imports / runtime guards
# -------------------------------

@lru_cache(maxsize=1)
def _import_tree_sitter() -> TSResult:
    try:
        import tree_sitter  # type: ignore
        return TSResult.ok_(tree_sitter)
    except Exception as exc:
        return TSResult.err(
            code="TREE_SITTER_MISSING",
            message="tree-sitter Python package is not installed.",
            detail=f"{type(exc).__name__}: {exc}",
        )

@lru_cache(maxsize=1)
def _import_language_pack() -> TSResult:
    try:
        import tree_sitter_language_pack as tslp # type: ignore
        return TSResult.ok_(tslp)
    except Exception as exc:
        return TSResult.err(
            code="LANGUAGE_PACK_MISSING",
            message="tree-sitter-language-pack is not installed.",
            detail=f"{type(exc).__name__}: {exc}",
        )


# -------------------------------
# Language Loading (v6: Language Pack focus)
# -------------------------------

@lru_cache(maxsize=10)
def load_language(lang_name: str) -> TSResult:
    """
    Load a tree-sitter Language instance using the language pack.
    Supported names: "python", "typescript", "tsx", "vue", "kotlin", etc.
    """
    lpr = _import_language_pack()
    if not lpr.ok:
        # Fallback for Kotlin only if pack is missing (backward compat)
        if lang_name == "kotlin":
            return load_kotlin_language_legacy()
        return lpr
    
    tslp = lpr.value
    try:
        lang = tslp.get_language(lang_name)
        if lang is None:
            return TSResult.err(code="LANGUAGE_NOT_FOUND", message=f"Language '{lang_name}' not found in pack.")
        return TSResult.ok_(lang)
    except Exception as exc:
        return TSResult.err(
            code="LANGUAGE_LOAD_FAILED",
            message=f"Failed to load language '{lang_name}' from pack.",
            detail=f"{type(exc).__name__}: {exc}"
        )

def load_kotlin_language() -> TSResult:
    """Convenience wrapper for Kotlin."""
    return load_language("kotlin")

def load_kotlin_language_legacy() -> TSResult:
    """Legacy loader for Kotlin if language-pack is not available."""
    tsr = _import_tree_sitter()
    if not tsr.ok: return tsr
    
    try:
        import tree_sitter_kotlin # type: ignore
        return TSResult.ok_(tree_sitter_kotlin.language())
    except Exception:
        pass
        
    return TSResult.err(code="KOTLIN_GRAMMAR_MISSING", message="Kotlin grammar not found.")


# -------------------------------
# Query Cache & Execution (v6: 0.25.2+ API)
# -------------------------------

class QueryCache:
    """
    Caches compiled tree-sitter Query objects.
    In 0.25.2+, queries are initialized via lang.query(str).
    """
    def __init__(self):
        self._cache: Dict[Tuple[int, str], Any] = {}

    def get_query(self, lang: Any, query_str: str) -> TSResult:
        key = (id(lang), query_str)
        if key in self._cache:
            return TSResult.ok_(self._cache[key])
        
        try:
            # 0.25.2+ Preferred way:
            query_obj = lang.query(query_str)
            self._cache[key] = query_obj
            return TSResult.ok_(query_obj)
        except Exception as exc:
            return TSResult.err(
                code="QUERY_COMPILE_FAILED",
                message="Failed to compile S-expression query.",
                detail=traceback.format_exc()
            )

# Global singleton
QUERY_CACHE = QueryCache()

def execute_query(
    lang: Any,
    root_node: Any,
    query_str: str,
    max_captures: int = 50_000
) -> TSResult:
    """
    High-performance query execution using 0.25.2+ API pattern.
    The cursor requires the query object at instantiation.
    """
    qr = QUERY_CACHE.get_query(lang, query_str)
    if not qr.ok: return qr
    query_obj = qr.value
    
    tsr = _import_tree_sitter()
    if not tsr.ok: return tsr
    ts_mod = tsr.value
    
    try:
        # Pattern for 0.25.x: Cursor(query) -> captures(node)
        cursor = ts_mod.QueryCursor(query_obj)
        
        try:
            raw_captures = cursor.captures(root_node)
        except TypeError:
            # Fallback for builds that still expect (query, node)
            raw_captures = cursor.captures(query_obj, root_node)
        
        results = []
        # Handle dict or iterator return
        if isinstance(raw_captures, dict):
            for cap_name, nodes in raw_captures.items():
                for node in nodes:
                    results.append((node, str(cap_name)))
            results.sort(key=lambda x: x[0].start_byte)
        else:
            for node, cap_ref in raw_captures:
                cap_name = query_obj.capture_names[cap_ref] if isinstance(cap_ref, int) else cap_ref
                results.append((node, str(cap_name)))
                if len(results) >= max_captures: break

        return TSResult.ok_(results[:max_captures])

    except Exception as exc:
        return TSResult.err(
            code="QUERY_EXEC_FAILED",
            message=f"Failed to execute tree-sitter query: {str(exc)}",
            detail=traceback.format_exc()
        )


# -------------------------------
# Parsing & Helpers
# -------------------------------

def parse_bytes(lang: Any, data: bytes) -> TSResult:
    """Parse bytes with the given Language."""
    tsr = _import_tree_sitter()
    if not tsr.ok: return tsr
    ts_mod = tsr.value

    try:
        parser = ts_mod.Parser(lang)
        tree = parser.parse(data)
        if tree is None:
            return TSResult.err(code="PARSE_FAILED", message="Parse returned None.")
        return TSResult.ok_(tree)
    except Exception as exc:
        return TSResult.err(
            code="PARSE_FAILED",
            message="tree-sitter parse failed.",
            detail=traceback.format_exc()
        )

def parse_file(lang: Any, path: Path) -> TSResult:
    """Parse a file as bytes. Returns TSResult(value=(tree, source_bytes))."""
    try:
        data = path.read_bytes()
    except Exception as exc:
        return TSResult.err(
            code="READ_FAILED",
            message=f"Failed to read file: {path}",
            detail=str(exc)
        )

    tr = parse_bytes(lang, data)
    if not tr.ok: return tr
    return TSResult.ok_((tr.value, data))

def node_text(node: Any, source: bytes) -> str:
    """Extract node text safely."""
    try:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
    except Exception:
        return ""

def node_line(node: Any) -> int:
    """Get 1-based start line."""
    try:
        return node.start_point[0] + 1
    except Exception:
        return 1

# --- Legacy Compatibility Helpers ---

def node_span_bytes(node: Any) -> Tuple[int, int]:
    try:
        return node.start_byte, node.end_byte
    except Exception:
        return 0, 0

def node_span_points(node: Any) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    try:
        sp = node.start_point
        ep = node.end_point
        return (int(sp[0]), int(sp[1])), (int(ep[0]), int(ep[1]))
    except Exception:
        return (0, 0), (0, 0)

def run_query(
    lang: Any,
    tree: Any,
    source: bytes,
    query: str,
    include_text: bool = False,
    max_captures: int = 50_000,
) -> TSResult:
    """Legacy run_query."""
    qr = execute_query(lang, tree.root_node, query, max_captures)
    if not qr.ok: return qr
    
    captures_out = []
    for node, cap_name in qr.value:
        sb, eb = node_span_bytes(node)
        (sr, sc), (er, ec) = node_span_points(node)
        row = {
            "name": cap_name,
            "start_byte": sb,
            "end_byte": eb,
            "start_point": [sr, sc],
            "end_point": [er, ec],
        }
        if include_text:
            row["text"] = node_text(node, source)
        captures_out.append(row)
        
    return TSResult.ok_(captures_out)
