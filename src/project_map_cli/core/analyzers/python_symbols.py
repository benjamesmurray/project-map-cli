# infra/digest_tool_v6/analyzers/python_symbols.py
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..common import tree_sitter_util as tsu
from ..common import root_resolver

# -------------------------
# S-Expression Queries
# -------------------------

# Query for classes, functions, and methods at module or class level
_PY_SYMBOL_QUERY = """
(module
  (class_definition
    name: (identifier) @name) @class)

(module
  (function_definition
    name: (identifier) @name) @function)

(class_definition
  body: (block
    (function_definition
      name: (identifier) @name) @method))

(module
  (decorated_definition
    definition: [
      (class_definition name: (identifier) @name)
      (function_definition name: (identifier) @name)
    ]) @decorated)
"""

# Query for imports
_PY_IMPORT_QUERY = """
(import_statement
  name: (dotted_name) @name) @import

(import_from_statement
  module_name: (dotted_name) @from
  name: (dotted_name) @name) @import_from
"""

# -------------------------
# Implementation
# -------------------------

def _resolve_qname(path: Path, root: Path, stack: List[str], name: str) -> str:
    base = root_resolver.normalize_qname(path, root, "")
    if stack:
        return f"{base}.{'.'.join(stack)}.{name}"
    return f"{base}.{name}"

def analyze(cfg: Config, py_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Python symbol table with import resolution for v6.
    """
    max_symbols_per_file = int(getattr(cfg, "max_py_symbols_per_file", 500) or 500)
    root = Path(cfg.root)
    
    lang_r = tsu.load_language("python")
    if not lang_r.ok:
        return {"error": "Python grammar missing"}
    lang = lang_r.value

    files_out = []
    symbols_out = []

    ordered = sorted(py_files, key=lambda p: p.relative_to(root).as_posix())

    for path in ordered:
        pid = pid_by_path.get(path, -1)
        rel = path.relative_to(root).as_posix()
        
        pr = tsu.parse_file(lang, path)
        if not pr.ok: continue
        
        tree, source = pr.value
        root_node = tree.root_node
        
        file_symbols = []
        file_imports = []

        # 1. Extract Symbols
        sqr = tsu.execute_query(lang, root_node, _PY_SYMBOL_QUERY)
        if sqr.ok:
            for node, cap_name in sqr.value:
                if cap_name == "name":
                    parent = node.parent
                    # Check for decorated parent
                    if parent and parent.parent and parent.parent.type == "decorated_definition":
                        parent = parent.parent
                    
                    kind = "unknown"
                    if parent.type == "class_definition": kind = "class"
                    elif parent.type == "function_definition":
                        # Check if inside a class
                        ancestor = parent.parent
                        while ancestor and ancestor.type != "class_definition" and ancestor.type != "module":
                            ancestor = ancestor.parent
                        kind = "method" if ancestor and ancestor.type == "class_definition" else "function"
                    elif parent.type == "decorated_definition":
                        inner = parent.child_by_field_name("definition")
                        kind = "class" if inner and inner.type == "class_definition" else "function"

                    name = tsu.node_text(node, source)
                    qn = _resolve_qname(path, root, [], name)
                    
                    # Extract decorators
                    decorators = []
                    if parent.type == "decorated_definition":
                        for child in parent.named_children:
                            if child.type == "decorator":
                                dec_text = tsu.node_text(child, source).strip("@")
                                decorators.append(dec_text)

                    file_symbols.append({
                        "name": name,
                        "kind": kind,
                        "pid": pid,
                        "ln": tsu.node_line(node),
                        "qname": qn,
                        "decorators": decorators
                    })

        # 2. Extract Imports
        iqr = tsu.execute_query(lang, root_node, _PY_IMPORT_QUERY)
        if iqr.ok:
            for node, cap_name in iqr.value:
                if cap_name == "name":
                    name = tsu.node_text(node, source)
                    from_mod = None
                    # If it's an import_from_statement, find the 'from' module
                    parent = node.parent
                    if parent and parent.type == "import_from_statement":
                        for child in parent.named_children:
                            if child.type == "dotted_name" and child != node:
                                from_mod = tsu.node_text(child, source)
                    
                    file_imports.append({
                        "name": name,
                        "from": from_mod,
                        "ln": tsu.node_line(node)
                    })

        # Enforce cap
        truncated = len(file_symbols) > max_symbols_per_file
        file_symbols = file_symbols[:max_symbols_per_file]
        
        symbols_out.extend(file_symbols)
        
        files_out.append({
            "pid": pid,
            "path": rel,
            "symbols": len(file_symbols),
            "imports": len(file_imports),
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
