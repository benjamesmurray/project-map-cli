# infra/digest_tool_v6/analyzers/inheritance.py
from __future__ import annotations

import networkx as nx
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..common import tree_sitter_util as tsu
from .symbol_registry import SymbolRegistry

def analyze(cfg: Config, kt_files: List[Path], pid_by_path: Dict[Path, int], registry: SymbolRegistry) -> nx.DiGraph:
    graph = nx.DiGraph()
    langr = tsu.load_kotlin_language()
    if not langr.ok:
        return graph
    lang = langr.value

    for path in kt_files:
        rel_path = path.relative_to(cfg.root).as_posix()
        pr = tsu.parse_file(lang, path)
        if not pr.ok:
            continue
        
        tree, source = pr.value
        _walk_inheritance(tree.root_node, source, rel_path, (), registry, graph)

    return graph

def _get_node_name(node: Any, source: bytes) -> str:
    for ch in getattr(node, "named_children", []) or []:
        if getattr(ch, "type", "") in ("simple_identifier", "type_identifier", "identifier"):
            return tsu.node_text(ch, source).strip()
    return ""

def _get_qname(package: str, stack: Tuple[str, ...], name: str) -> str:
    parts = []
    if package:
        parts.append(package)
    parts.extend([p for p in stack if p])
    if name:
        parts.append(name)
    return ".".join(parts) if parts else name

def _resolve_super_type(name: str, rel_path: str, registry: SymbolRegistry) -> str:
    # Very simple resolution: check imports and same package
    file_info = registry.files.get(rel_path)
    if not file_info:
        return name
        
    package = file_info["package"]
    imports = file_info["imports"]
    
    # 1. Check direct imports
    for imp in imports:
        if imp.endswith("." + name):
            return imp
            
    # 2. Check same package
    candidate = package + "." + name if package else name
    if registry.get_symbol(candidate):
        return candidate
        
    return name

def _walk_inheritance(
    node: Any, 
    source: bytes, 
    rel_path: str, 
    stack: Tuple[str, ...], 
    registry: SymbolRegistry,
    graph: nx.DiGraph
):
    ntype = getattr(node, "type", "") or ""
    
    new_stack = stack
    if ntype in ("class_declaration", "object_declaration", "interface_declaration", "enum_class_declaration"):
        name = _get_node_name(node, source)
        if name:
            file_info = registry.files.get(rel_path)
            package = file_info["package"] if file_info else ""
            qname = _get_qname(package, stack, name)
            
            if not graph.has_node(qname):
                graph.add_node(qname)

            # Find supertypes
            for ch in getattr(node, "named_children", []) or []:
                # Try both singular and plural based on different TS grammar versions
                if ch.type in ("delegation_specifiers", "delegation_specifier"):
                    for spec in getattr(ch, "named_children", []) or []:
                        # spec can be user_type, constructor_invocation, etc.
                        txt = tsu.node_text(spec, source).strip()
                        # Extract the base type name (handle generics)
                        # e.g. Processor<String, ...> -> Processor
                        super_name = txt.split("<")[0].split("(")[0].strip()
                        if super_name:
                            resolved_super = _resolve_super_type(super_name, rel_path, registry)
                            graph.add_edge(qname, resolved_super, kind="extends")

            new_stack = stack + (name,)

    for ch in getattr(node, "named_children", []) or []:
        _walk_inheritance(ch, source, rel_path, new_stack, registry, graph)
