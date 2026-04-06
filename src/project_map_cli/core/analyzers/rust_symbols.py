# infra/digest_tool_v6/analyzers/rust_symbols.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..config import Config
from ..common import tree_sitter_util as tsu

# -------------------------
# S-Expression Queries
# -------------------------

_RS_SYMBOL_QUERY = """
(function_item
  name: (identifier) @name) @function

(struct_item
  name: (type_identifier) @name) @struct

(enum_item
  name: (type_identifier) @name) @enum

(trait_item
  name: (type_identifier) @name) @trait

(mod_item
  name: (identifier) @name) @module

(impl_item
  type: (type_identifier) @name) @impl
"""

# -------------------------
# Implementation
# -------------------------

def analyze(cfg: Config, rs_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Rust symbol table for v6.
    """
    max_symbols_per_file = 500
    root = Path(cfg.root)
    
    lang_r = tsu.load_language("rust")
    if not lang_r.ok:
        return {"error": "Rust grammar missing"}
    lang = lang_r.value

    files_out = []
    symbols_out = []

    ordered = sorted(rs_files, key=lambda p: p.relative_to(root).as_posix())

    for path in ordered:
        pid = pid_by_path.get(path, -1)
        rel = path.relative_to(root).as_posix()
        
        pr = tsu.parse_file(lang, path)
        if not pr.ok: continue
        
        tree, source = pr.value
        root_node = tree.root_node
        
        file_symbols = []

        sqr = tsu.execute_query(lang, root_node, _RS_SYMBOL_QUERY)
        if sqr.ok:
            for node, cap_name in sqr.value:
                if cap_name == "name":
                    parent = node.parent
                    kind = "unknown"
                    if parent.type == "function_item": kind = "function"
                    elif parent.type == "struct_item": kind = "struct"
                    elif parent.type == "enum_item": kind = "enum"
                    elif parent.type == "trait_item": kind = "trait"
                    elif parent.type == "mod_item": kind = "module"
                    elif parent.type == "impl_item": kind = "impl"
                    
                    name = tsu.node_text(node, source)
                    # Simplified qname for Rust: rel_path::name
                    # (In real Rust this depends on mod structure, but this is a good heuristic)
                    mod_path = rel.replace(".rs", "").replace("/", "::")
                    qn = f"{mod_path}::{name}"

                    file_symbols.append({
                        "name": name,
                        "kind": kind,
                        "pid": pid,
                        "ln": tsu.node_line(node),
                        "qname": qn,
                    })

        # Enforce cap
        truncated = len(file_symbols) > max_symbols_per_file
        file_symbols = file_symbols[:max_symbols_per_file]
        
        symbols_out.extend(file_symbols)
        
        files_out.append({
            "pid": pid,
            "path": rel,
            "symbols": len(file_symbols),
            "truncated": truncated
        })

    # Sort
    symbols_out.sort(key=lambda s: (s["pid"], s["ln"], s["qname"]))
    files_out.sort(key=lambda f: f["pid"])

    return {
        "version": "6.0",
        "files": files_out,
        "symbols": symbols_out
    }
