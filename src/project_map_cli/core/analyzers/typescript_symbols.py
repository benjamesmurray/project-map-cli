# infra/digest_tool_v6/analyzers/typescript_symbols.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..common import tree_sitter_util as tsu
from ..common import root_resolver

# -------------------------
# S-Expression Queries
# -------------------------

# Query for classes, interfaces, and functions
_TS_SYMBOL_QUERY = """
(export_statement
  declaration: [
    (class_declaration name: (type_identifier) @name)
    (interface_declaration name: (type_identifier) @name)
    (type_alias_declaration name: (type_identifier) @name)
    (function_declaration name: (identifier) @name)
    (lexical_declaration (variable_declarator name: (identifier) @name))
  ]
) @exported

(class_declaration 
  name: (type_identifier) @name) @class

(interface_declaration 
  name: (type_identifier) @name) @interface

(type_alias_declaration
  name: (type_identifier) @name) @type

(function_declaration 
  name: (identifier) @name) @function

(method_definition
  name: (property_identifier) @name) @method

(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: [(arrow_function) (function_expression)])) @function_val
"""

# Query for D3 structural chains
_D3_CHAIN_QUERY = """
(call_expression
  function: (member_expression
    object: (call_expression) @parent
    property: (property_identifier) @method
    (#match? @method "^(select|selectAll|data|join|enter|append)$")
  )
) @chain
"""

# -------------------------
# Implementation
# -------------------------

def _is_type_kind(kind: str) -> bool:
    return kind in ("interface", "type")

def _resolve_qname(path: Path, root: Path, stack: List[str], name: str) -> str:
    # Use root_resolver for consistent naming
    base = root_resolver.normalize_qname(path, root, "")
    if stack:
        return f"{base}.{'.'.join(stack)}.{name}"
    return f"{base}.{name}"

def analyze(cfg: Config, ts_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    TypeScript/JS symbol table with D3 structural analysis.
    """
    max_symbols_per_file = int(getattr(cfg, "max_ts_symbols_per_file", 500) or 500)
    root = Path(cfg.root)
    
    # Load languages
    ts_lang_r = tsu.load_language("typescript")
    tsx_lang_r = tsu.load_language("tsx")
    
    if not ts_lang_r.ok or not tsx_lang_r.ok:
        return {"error": "TS/TSX grammars missing"}

    ts_lang = ts_lang_r.value
    tsx_lang = tsx_lang_r.value

    files_out = []
    symbols_out = []

    # Sort files for determinism
    ordered = sorted(ts_files, key=lambda p: p.relative_to(root).as_posix())

    for path in ordered:
        pid = pid_by_path.get(path, -1)
        rel = path.relative_to(root).as_posix()
        
        # Select grammar based on extension
        lang = tsx_lang if path.suffix.lower() in (".tsx", ".jsx") else ts_lang
        
        pr = tsu.parse_file(lang, path)
        if not pr.ok:
            print(f"DEBUG: Parse failed for {rel}: {pr.error}")
            continue
        
        tree, source = pr.value
        root_node = tree.root_node
        
        file_symbols = []
        
        # 1. Extract Symbols
        sqr = tsu.execute_query(lang, root_node, _TS_SYMBOL_QUERY)
        if not sqr.ok:
            print(f"DEBUG: Query failed for {rel}: {sqr.error}")
        else:
            if not sqr.value:
                # print(f"DEBUG: No matches for {rel}")
                pass
            else:
                print(f"DEBUG: Found {len(sqr.value)} captures in {rel}")
            # Group captures by their parent node
            node_to_info = {}
            for node, cap_name in sqr.value:
                if cap_name == "name":
                    parent = node.parent
                    # Handle lexical_declaration -> variable_declarator -> name
                    if parent and parent.type == "variable_declarator":
                        parent = parent.parent # lexical_declaration
                    
                    if parent not in node_to_info:
                        node_to_info[parent] = {"name": tsu.node_text(node, source), "kind": "unknown"}
                    else:
                        node_to_info[parent]["name"] = tsu.node_text(node, source)
                else:
                    kind = cap_name.replace("_val", "").replace("exported", "unknown")
                    # If it's 'exported', we'll refine the kind from the declaration child later if needed,
                    # but usually the other captures (class, interface, etc.) will also fire for the same nodes.
                    if node not in node_to_info:
                        node_to_info[node] = {"name": "", "kind": kind}
                    elif kind != "unknown":
                        node_to_info[node]["kind"] = kind

            for node, info in node_to_info.items():
                name = info["name"]
                if not name: continue
                
                kind = info["kind"]
                # Refine kind if it was marked as exported/unknown
                if kind == "unknown" or kind == "exported":
                    if node.type == "class_declaration": kind = "class"
                    elif node.type == "interface_declaration": kind = "interface"
                    elif node.type == "type_alias_declaration": kind = "type"
                    elif node.type == "function_declaration": kind = "function"
                    elif node.type == "method_definition": kind = "method"
                    elif node.type == "lexical_declaration": kind = "function" # simplified
                    elif node.type == "export_statement":
                        # Look at the declaration child
                        for child in node.named_children:
                            if child.type == "class_declaration": kind = "class"
                            elif child.type == "interface_declaration": kind = "interface"
                            elif child.type == "function_declaration": kind = "function"
                            elif child.type == "lexical_declaration": kind = "function"
                
                qn = _resolve_qname(path, root, [], name)
                
                file_symbols.append({
                    "name": name,
                    "kind": kind,
                    "pid": pid,
                    "ln": tsu.node_line(node),
                    "end_ln": node.end_point[0] + 1,
                    "qname": qn,
                    "isTypeOnly": _is_type_kind(kind)
                })

        # 2. Extract D3 Chains (Simplified visitor approach)
        d3r = tsu.execute_query(lang, root_node, _D3_CHAIN_QUERY)
        if d3r.ok:
            # We look for call chains and associate them with the containing symbol
            for node, cap_name in d3r.value:
                if cap_name == "chain":
                    # For a chain, we want to know its context
                    ln = tsu.node_line(node)
                    method = ""
                    # Try to find the specific method in this step
                    for child in node.named_children:
                        if child.type == "member_expression":
                            for gc in child.named_children:
                                if gc.type == "property_identifier":
                                    method = tsu.node_text(gc, source)
                    
                    if method:
                        # Find the symbol containing this line (narrowest range)
                        containing_sym = None
                        smallest_range = float('inf')
                        for sym in file_symbols:
                            if sym["ln"] <= ln <= sym.get("end_ln", ln):
                                sym_range = sym.get("end_ln", ln) - sym["ln"]
                                if sym_range <= smallest_range:
                                    containing_sym = sym
                                    smallest_range = sym_range
                        
                        if containing_sym:
                            if "external_patterns" not in containing_sym:
                                containing_sym["external_patterns"] = []
                            
                            # Add to existing pattern or create new
                            d3_pattern = next((p for p in containing_sym["external_patterns"] if p["library"] == "d3"), None)
                            if not d3_pattern:
                                d3_pattern = {
                                    "library": "d3",
                                    "pattern": "structural-chain",
                                    "chain": [],
                                    "ln": ln
                                }
                                containing_sym["external_patterns"].append(d3_pattern)
                            
                            if method not in d3_pattern["chain"]:
                                d3_pattern["chain"].append(method)

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

    # Sort for determinism
    symbols_out.sort(key=lambda s: (s["pid"], s["ln"], s["qname"]))
    files_out.sort(key=lambda f: f["pid"])

    return {
        "version": "6.0",
        "files": files_out,
        "symbols": symbols_out
    }
