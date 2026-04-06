# infra/digest_tool_v6/analyzers/go_symbols.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..config import Config
from ..common import tree_sitter_util as tsu
from ..common import root_resolver

# -------------------------
# S-Expression Queries
# -------------------------

_GO_SYMBOL_QUERY = """
(package_clause
  (package_identifier) @package)

(function_declaration
  name: (identifier) @name) @function

(method_declaration
  name: (field_identifier) @name) @method

(type_declaration
  (type_spec
    name: (type_identifier) @name)) @type
"""

# -------------------------
# Implementation
# -------------------------

def analyze(cfg: Config, go_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Go symbol table for v6.
    """
    max_symbols_per_file = 500
    root = Path(cfg.root)
    
    lang_r = tsu.load_language("go")
    if not lang_r.ok:
        return {"error": "Go grammar missing"}
    lang = lang_r.value

    files_out = []
    symbols_out = []

    ordered = sorted(go_files, key=lambda p: p.relative_to(root).as_posix())

    for path in ordered:
        pid = pid_by_path.get(path, -1)
        rel = path.relative_to(root).as_posix()
        
        pr = tsu.parse_file(lang, path)
        if not pr.ok: continue
        
        tree, source = pr.value
        root_node = tree.root_node
        
        file_symbols = []
        package_name = ""

        sqr = tsu.execute_query(lang, root_node, _GO_SYMBOL_QUERY)
        if sqr.ok:
            for node, cap_name in sqr.value:
                if cap_name == "package":
                    package_name = tsu.node_text(node, source)
                elif cap_name == "name":
                    parent = node.parent
                    kind = "unknown"
                    if parent.type == "function_declaration": kind = "function"
                    elif parent.type == "method_declaration": kind = "method"
                    elif parent.type == "type_spec": kind = "type"
                    
                    name = tsu.node_text(node, source)
                    # Simplified qname for Go: package.name
                    qn = f"{package_name}.{name}" if package_name else name

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
            "package": package_name,
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
