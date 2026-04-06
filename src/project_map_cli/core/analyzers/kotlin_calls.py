# infra/digest_tool_v6/analyzers/kotlin_calls.py
from __future__ import annotations

import networkx as nx
import sys
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from ..config import Config
from ..common import tree_sitter_util as tsu
from .symbol_registry import SymbolRegistry, SymbolInfo

class CallResolver:
    def __init__(self, registry: SymbolRegistry):
        self.registry = registry
        self.graph = nx.DiGraph()

    def resolve_call(self, call_name: str, file_path: str, local_stack: Tuple[str, ...], receiver: Optional[str] = None) -> Optional[str]:
        file_info = self.registry.files.get(file_path)
        if not file_info:
            return None
            
        package = file_info["package"]
        imports = file_info["imports"]
        
        # If we have a receiver (e.g., Obj in Obj.foo()), resolve the receiver first
        if receiver:
            # 1. Check direct imports for receiver
            for imp in imports:
                if imp.endswith("." + receiver):
                    return imp + "." + call_name
                if imp == receiver:
                    return imp + "." + call_name
            
            # 2. Check same package for receiver
            candidate = package + "." + receiver if package else receiver
            if self.registry.get_symbol(candidate):
                return candidate + "." + call_name
                
            # 3. Check if receiver is in the local stack (nested class/object)
            current_stack = local_stack
            while True:
                candidate = ".".join(filter(None, [package] + list(current_stack) + [receiver]))
                if self.registry.get_symbol(candidate):
                    return candidate + "." + call_name
                if not current_stack:
                    break
                current_stack = current_stack[:-1]
                
            return None

        # Original logic for simple calls (no receiver)
        # 1. Check local scope (same class/file)
        current_stack = local_stack
        while True:
            candidate = ".".join(filter(None, [package] + list(current_stack) + [call_name]))
            if self.registry.get_symbol(candidate):
                return candidate
            if not current_stack:
                break
            current_stack = current_stack[:-1]
            
        # 2. Check direct imports
        for imp in imports:
            if imp.endswith("." + call_name):
                return imp
            if imp == call_name:
                return imp
                
        # 3. Check star imports
        for imp in imports:
            if imp.endswith(".*"):
                candidate = imp[:-1] + call_name
                if self.registry.get_symbol(candidate):
                    return candidate
                    
        # 4. Check same package
        candidate = package + "." + call_name if package else call_name
        if self.registry.get_symbol(candidate):
            return candidate
            
        return None

def analyze(cfg: Config, kt_files: List[Path], pid_by_path: Dict[Path, int], registry: SymbolRegistry) -> nx.DiGraph:
    resolver = CallResolver(registry)
    langr = tsu.load_kotlin_language()
    lang = langr.value

    for path in kt_files:
        rel_path = path.relative_to(cfg.root).as_posix()
        pid = pid_by_path.get(path, -1)
        pr = tsu.parse_file(lang, path)
        if not pr.ok:
            continue
            
        tree, source = pr.value
        
        # Use broad call expression query
        qr = tsu.run_query(lang, tree, source, "(call_expression) @call", include_text=True)
        if not qr.ok:
            continue
            
        _walk_with_calls(tree.root_node, source, rel_path, (), None, resolver, qr.value or [])

    return resolver.graph

def _get_node_name(node: Any, source: bytes) -> str:
    for ch in getattr(node, "named_children", []) or []:
        if getattr(ch, "type", "") in ("simple_identifier", "type_identifier", "identifier"):
            return tsu.node_text(ch, source).strip()
    return ""

def _walk_with_calls(
    node: Any, 
    source: bytes, 
    rel_path: str, 
    stack: Tuple[str, ...], 
    current_func: Optional[str], 
    resolver: CallResolver,
    call_captures: List[Dict[str, Any]]
):
    ntype = getattr(node, "type", "") or ""
    sb, eb = tsu.node_span_bytes(node)
    
    new_stack = stack
    new_func = current_func
    
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
            new_stack = stack + (name,)
            
    elif ntype == "function_declaration":
        name = _get_node_name(node, source)
        if name:
            file_info = resolver.registry.files.get(rel_path)
            pkg = file_info["package"] if file_info else ""
            new_func = ".".join(filter(None, [pkg] + list(stack) + [name]))
            new_stack = stack + (name,) # Walk into functions for local calls
            
    elif ntype == "call_expression":
        # Find the capture for this exact node
        call_row = None
        for c in call_captures:
            if c["start_byte"] == sb and c["end_byte"] == eb:
                call_row = c
                break
        
        if call_row and current_func:
            txt = call_row.get("text", "")
            
            # Kafka Bridge: Detect RoutedValue(Route.X, ...)
            if "RoutedValue" in txt and "Route." in txt:
                m = re.search(r"Route\.([A-Za-z_][A-Za-z0-9_]*)", txt)
                if m:
                    route = m.group(1)
                    resolver.graph.add_edge(current_func, f"KAFKA_TOPIC:Route.{route}", kind="produce")

            # Try to resolve call using receiver if captured
            match_name = ""
            receiver = ""
            
            # Check for captured names in this range
            for c in call_captures:
                if c["start_byte"] >= sb and c["end_byte"] <= eb:
                    if c.get("capture") == "call.name":
                        match_name = c.get("text", "")
                    elif c.get("capture") == "call.receiver":
                        receiver = c.get("text", "")

            if match_name:
                resolved = resolver.resolve_call(match_name, rel_path, stack, receiver=receiver if receiver else None)
                if resolved:
                    resolver.graph.add_edge(current_func, resolved, pid=resolver.registry.files[rel_path]["pid"], ln=node.start_point[0] + 1)

    for ch in getattr(node, "named_children", []) or []:
        _walk_with_calls(ch, source, rel_path, new_stack, new_func, resolver, call_captures)
