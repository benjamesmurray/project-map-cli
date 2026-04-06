# infra/digest_tool_v6/analyzers/vue_analyzer.py
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

# Query to find top-level blocks in .vue
_VUE_BLOCKS_QUERY = """
(element
  (start_tag
    (tag_name) @tag)
  (text) @body
  (end_tag)) @block
"""

# Query for script setup macros
_VUE_SETUP_MACROS_QUERY = """
(call_expression
  function: (identifier) @macro
  arguments: (arguments
    (object) @content)
  (#match? @macro "^(defineProps|defineEmits)$")
)
"""

# Query for options API props/emits
_VUE_OPTIONS_QUERY = """
(export_statement
  declaration: (object
    (pair
      key: (property_identifier) @key
      value: [(array) (object)] @val
      (#match? @key "^(props|emits)$")
    )
  )
)
"""

# -------------------------
# Implementation
# -------------------------

def _extract_used_components(template_text: str) -> List[str]:
    """
    Heuristic: Find PascalCase tags in template that look like components.
    """
    # Simple regex for custom component tags <Component ...> or <component-name ...>
    # We focus on PascalCase as it's the standard for imported components in Vue.
    pattern = re.compile(r"<([A-Z][a-zA-Z0-9]+)")
    matches = pattern.findall(template_text)
    return sorted(list(set(matches)))

def analyze(cfg: Config, vue_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Vue SFC Analyzer for v6.
    """
    root = Path(cfg.root)
    vue_lang_r = tsu.load_language("vue")
    ts_lang_r = tsu.load_language("typescript")
    
    if not vue_lang_r.ok or not ts_lang_r.ok:
        return {"error": "Vue/TS grammars missing"}

    vue_lang = vue_lang_r.value
    ts_lang = ts_lang_r.value

    components_out = []
    
    # Deterministic file order
    ordered = sorted(vue_files, key=lambda p: p.relative_to(root).as_posix())

    for path in ordered:
        pid = pid_by_path.get(path, -1)
        rel = path.relative_to(root).as_posix()
        
        pr = tsu.parse_file(vue_lang, path)
        if not pr.ok: continue
        
        tree, source = pr.value
        root_node = tree.root_node
        
        comp_info = {
            "name": path.name,
            "pid": pid,
            "qname": root_resolver.normalize_qname(path, root, ""),
            "props": [],
            "emits": [],
            "used_components": []
        }

        # 1. Parse Blocks
        blocks_r = tsu.execute_query(vue_lang, root_node, _VUE_BLOCKS_QUERY)
        if blocks_r.ok:
            for node, cap_name in blocks_r.value:
                # This query is a bit complex due to tree-sitter-vue's structure
                # We'll use a simpler approach if query fails or is messy
                pass

        # Manual block split as fallback/reliability (SFCs are usually well-structured)
        text = source.decode("utf-8", errors="ignore")
        
        # Extract Template Components
        template_match = re.search(r"<template>(.*?)</template>", text, re.DOTALL)
        if template_match:
            comp_info["used_components"] = _extract_used_components(template_match.group(1))

        # Extract Script Info (Setup or Options)
        script_match = re.search(r"<script\b[^>]*>(.*?)</script>", text, re.DOTALL)
        if script_match:
            script_text = script_match.group(1)
            script_bytes = script_text.encode("utf-8")
            
            # Parse script with TS grammar
            str_r = tsu.parse_bytes(ts_lang, script_bytes)
            if str_r.ok:
                script_tree = str_r.value
                s_root = script_tree.root_node
                
                # Check for setup macros
                setup_r = tsu.execute_query(ts_lang, s_root, _VUE_SETUP_MACROS_QUERY)
                if setup_r.ok:
                    for node, cap_name in setup_r.value:
                        if cap_name == "macro":
                            macro_name = tsu.node_text(node, script_bytes)
                            # Extract keys from the object argument
                            parent = node.parent
                            if parent:
                                for arg in parent.named_children:
                                    if arg.type == "arguments":
                                        for obj in arg.named_children:
                                            if obj.type == "object":
                                                for pair in obj.named_children:
                                                    if pair.type == "pair":
                                                        for key_node in pair.named_children:
                                                            if key_node.type == "property_identifier":
                                                                key_text = tsu.node_text(key_node, script_bytes)
                                                                if macro_name == "defineProps":
                                                                    comp_info["props"].append(key_text)
                                                                else:
                                                                    comp_info["emits"].append(key_text)

                # Check for Options API
                opt_r = tsu.execute_query(ts_lang, s_root, _VUE_OPTIONS_QUERY)
                if opt_r.ok:
                    for node, cap_name in opt_r.value:
                        if cap_name == "key":
                            key_text = tsu.node_text(node, script_bytes)
                            val_node = node.next_named_sibling
                            if val_node:
                                # Extract keys from array or object
                                if val_node.type == "array":
                                    for item in val_node.named_children:
                                        if item.type == "string":
                                            val = tsu.node_text(item, script_bytes).strip("'\"")
                                            if key_text == "props": comp_info["props"].append(val)
                                            else: comp_info["emits"].append(val)
                                elif val_node.type == "object":
                                    for pair in val_node.named_children:
                                        if pair.type == "pair":
                                            for kn in pair.named_children:
                                                if kn.type == "property_identifier":
                                                    val = tsu.node_text(kn, script_bytes)
                                                    if key_text == "props": comp_info["props"].append(val)
                                                    else: comp_info["emits"].append(val)

        # Cleanup and de-dup
        comp_info["props"] = sorted(list(set(comp_info["props"])))
        comp_info["emits"] = sorted(list(set(comp_info["emits"])))
        components_out.append(comp_info)

    components_out.sort(key=lambda c: c["qname"])

    return {
        "version": "6.0",
        "components": components_out
    }
