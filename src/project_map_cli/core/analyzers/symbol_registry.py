# infra/digest_tool_v6/analyzers/symbol_registry.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from ..config import Config
from ..common import tree_sitter_util as tsu

@dataclass
class SymbolInfo:
    name: str
    kind: str
    pid: int
    ln: int
    qname: str
    file_path: str
    package: str
    imports: List[str]
    implements: List[str] = None
    annotations: List[str] = None

class SymbolRegistry:
    def __init__(self):
        self.symbols: Dict[str, SymbolInfo] = {}
        self.files: Dict[str, Dict[str, Any]] = {}

    def add_symbol(self, info: SymbolInfo):
        self.symbols[info.qname] = info

    def get_symbol(self, qname: str) -> Optional[SymbolInfo]:
        return self.symbols.get(qname)

def _get_qname(pkg: str, stack: Tuple[str, ...], name: str) -> str:
    parts = []
    if pkg:
        parts.append(pkg)
    parts.extend([p for p in stack if p])
    if name:
        parts.append(name)
    return ".".join(parts) if parts else name

def _get_node_name(node: Any, source: bytes) -> str:
    for ch in getattr(node, "named_children", []) or []:
        if getattr(ch, "type", "") in ("simple_identifier", "type_identifier", "identifier"):
            return tsu.node_text(ch, source).strip()
    return ""

def _walk(
    node: Any,
    source: bytes,
    pid: int,
    rel_path: str,
    package: str,
    stack: Tuple[str, ...],
    registry: SymbolRegistry,
    imports: List[str],
):
    ntype = getattr(node, "type", "") or ""
    
    new_stack = stack
    is_classlike = ntype in (
        "class_declaration", 
        "object_declaration", 
        "interface_declaration", 
        "enum_class_declaration",
        "companion_object"
    )

    if is_classlike:
        name = _get_node_name(node, source)
        if not name and ntype == "companion_object":
            name = "Companion"
            
        if name:
            qname = _get_qname(package, stack, name)
            registry.add_symbol(SymbolInfo(
                name=name,
                kind="class",
                pid=pid,
                ln=node.start_point[0] + 1,
                qname=qname,
                file_path=rel_path,
                package=package,
                imports=imports
            ))
            new_stack = stack + (name,)
            
    elif ntype == "function_declaration":
        name = _get_node_name(node, source)
        if name:
            qname = _get_qname(package, stack, name)
            registry.add_symbol(SymbolInfo(
                name=name,
                kind="function",
                pid=pid,
                ln=node.start_point[0] + 1,
                qname=qname,
                file_path=rel_path,
                package=package,
                imports=imports
            ))
            new_stack = stack + (name,)
            
    for ch in getattr(node, "named_children", []) or []:
        _walk(ch, source, pid, rel_path, package, new_stack, registry, imports)

def analyze(cfg: Config, kt_files: List[Path], pid_by_path: Dict[Path, int]) -> SymbolRegistry:
    registry = SymbolRegistry()
    langr = tsu.load_kotlin_language()
    if not langr.ok:
        return registry
    lang = langr.value

    for path in kt_files:
        pid = pid_by_path.get(path, -1)
        rel_path = path.relative_to(cfg.root).as_posix()
        
        pr = tsu.parse_file(lang, path)
        if not pr.ok:
            continue
        
        tree, source = pr.value
        root_node = getattr(tree, "root_node", None)
        if root_node is None:
            continue

        package = ""
        imports = []
        for ch in getattr(root_node, "named_children", []) or []:
            nt = getattr(ch, "type", "")
            txt = tsu.node_text(ch, source).strip()
            if not txt:
                continue

            if nt == "package_header" or txt.startswith("package "):
                package = txt.replace("package", "").strip().rstrip(";").strip()
                continue

            if nt == "import_header" or txt.startswith("import "):
                imp = txt.replace("import", "").strip().rstrip(";").strip()
                if imp:
                    imports.append(imp)

        # De-dup imports
        imports = sorted(list(set(imports)))

        registry.files[rel_path] = {
            "package": package,
            "imports": imports,
            "pid": pid
        }
        
        _walk(root_node, source, pid, rel_path, package, (), registry, imports)

    return registry
