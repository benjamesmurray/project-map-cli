import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from project_map_cli.core.query_engine import QueryEngine
from project_map_cli.core.common import tree_sitter_util as tsu

class HydrationTools:
    def __init__(self, engine: QueryEngine):
        self.engine = engine

    def fetch_symbol(self, file_path: str, symbol_name: str) -> str:
        """AST-level extraction of a symbol's raw code string."""
        ext = Path(file_path).suffix.lower()
        lang_map = {
            '.py': 'python', '.ts': 'typescript', '.tsx': 'tsx', 
            '.kt': 'kotlin', '.go': 'go', '.rs': 'rust',
            '.vue': 'vue', '.js': 'javascript', '.jsx': 'javascript'
        }
        lang_name = lang_map.get(ext)
        if not lang_name:
            return f"Error: Unsupported file extension {ext}"

        lang_r = tsu.load_language(lang_name)
        if not lang_r.ok:
            return f"Error: Failed to load language {lang_name} - {lang_r.error.message if lang_r.error else 'Unknown error'}"

        lang = lang_r.value
        full_path = Path(self.engine.project_root) / file_path
        if not full_path.exists():
            return f"Error: File not found: {file_path}"

        pr = tsu.parse_file(lang, full_path)
        if not pr.ok:
            return f"Error: Failed to parse file {file_path} - {pr.error.message if pr.error else 'Unknown error'}"

        tree, source = pr.value

        def walk(node):
            """Generic walker to find first node with identifier matching symbol_name."""
            for child in getattr(node, "named_children", []) or []:
                ntype = getattr(child, "type", "")
                if ntype in ("identifier", "name", "type_identifier", "property_identifier", "simple_identifier"):
                    name = tsu.node_text(child, source).strip()
                    if name == symbol_name:
                        # Return parent node as it likely contains the body/definition
                        return node
                res = walk(child)
                if res:
                    return res
            return None

        target_node = walk(tree.root_node)
        if target_node:
            return tsu.node_text(target_node, source)

        return f"Error: Symbol '{symbol_name}' not found in AST for {file_path}"

    def check_blast_radius(self, file_path: str, symbol_name: str, fanout_cap: int = 50) -> List[Dict[str, Any]]:
        """Determine what components depend on this symbol, including Python imports."""
        matches = self.engine.search_symbols(symbol_name)
        target_fqn = None
        for m in matches:
            m_path = m.get('path', '')
            if m_path.endswith(file_path) or file_path.endswith(m_path):
                target_fqn = m.get('qname') or m.get('name')
                break
        
        # Fallback to deduce module name from path
        p = Path(file_path)
        rel_p = p.with_suffix("")
        mod_name = ".".join(rel_p.parts)
        
        if not target_fqn:
            target_fqn = f"{mod_name}.{symbol_name}"

        queue = [target_fqn]
        visited = set()
        count = 0
        results = []

        # Load Python imports for additional blast radius
        python_imports = {}
        try:
            imports_shard = self.engine.read_json_shard("analyzers.repo_only.json")
            for edge in imports_shard.get("edges", []):
                dst = edge.get("dst", "")
                if dst not in python_imports:
                    python_imports[dst] = []
                python_imports[dst].append(edge)
        except Exception:
            pass

        while queue and count < fanout_cap:
            current = queue.pop(0)
            if not current or current in visited:
                continue
            visited.add(current)
            count += 1

            # 1. Graph callers (Kotlin, etc.)
            callers = self.engine.get_callers(current)
            for caller in callers:
                c_fqn = caller["fqn"]
                if c_fqn not in visited:
                    queue.append(c_fqn)
                    results.append({
                        "name": c_fqn,
                        "path": caller.get("path", "unknown"),
                        "ln": caller.get("ln", "unknown"),
                        "via": current,
                        "type": "call"
                    })
            
            # 2. Python imports
            # We match 'current' module or its parent modules against 'dst' in python_imports
            # Also handle the 'src.' prefix mismatch
            search_targets = [current]
            if current.startswith("src."):
                search_targets.append(current[4:])
            else:
                search_targets.append(f"src.{current}")
            
            # Check parent modules if current is a symbol inside a module
            parts = current.split(".")
            if len(parts) > 1:
                parent = ".".join(parts[:-1])
                search_targets.append(parent)
                if parent.startswith("src."):
                    search_targets.append(parent[4:])
                else:
                    search_targets.append(f"src.{parent}")

            for target in set(search_targets):
                for dst_mod, edges in python_imports.items():
                    if dst_mod == target or dst_mod.startswith(target + "."):
                        for edge in edges:
                            src_mod = edge.get("src")
                            if src_mod and src_mod not in visited:
                                queue.append(src_mod)
                                pid = edge.get("pid")
                                path = self.engine.resolve_pids([str(pid)]).get(str(pid), "unknown") if pid is not None else "unknown"
                                results.append({
                                    "name": src_mod,
                                    "path": path,
                                    "ln": edge.get("ln", "unknown"),
                                    "via": current,
                                    "type": "import"
                                })
        
        return results

    def semantic_search(self, query: str) -> List[Dict[str, Any]]:
        """Naive keyword-based search over the project index."""
        keywords = [w.lower() for w in query.split() if len(w) > 3]
        if not keywords:
            keywords = [query.lower()]

        results = []
        try:
            latest_path = self.engine.get_latest_digest_path()
        except RuntimeError:
            return []

        fi_dir = Path(latest_path) / "files_index"
        if not fi_dir.exists():
            return []

        pids_needed = set()

        for shard_file in fi_dir.glob("*.min.json"):
            try:
                with open(shard_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for file_obj in data.get("files", []):
                    score = 0
                    matched_items = []
                    
                    # Search in classes
                    for c in file_obj.get("c", []):
                        name = c.get("name", "").lower()
                        doc = str(c.get("doc", "")).lower()
                        for kw in keywords:
                            if kw in name:
                                score += 5
                                matched_items.append(f"class:{c['name']}")
                            elif kw in doc:
                                score += 2
                                matched_items.append(f"doc:{c['name']}")

                    # Search in functions
                    for f_obj in file_obj.get("f", []):
                        name = f_obj.get("name", "").lower()
                        doc = str(f_obj.get("doc", "")).lower()
                        for kw in keywords:
                            if kw in name:
                                score += 5
                                matched_items.append(f"func:{f_obj['name']}")
                            elif kw in doc:
                                score += 2
                                matched_items.append(f"doc:{f_obj['name']}")

                    # Search in methods
                    for m in file_obj.get("m", []):
                        name = m.get("name", "").lower()
                        for kw in keywords:
                            if kw in name:
                                score += 3
                                matched_items.append(f"method:{m['name']}")

                    if score > 0:
                        pid = file_obj.get("p")
                        pids_needed.add(str(pid))
                        results.append({
                            "pid": pid,
                            "score": score,
                            "matches": list(set(matched_items))
                        })
            except Exception:
                continue

        if not results:
            return []

        path_map = self.engine.resolve_pids(list(pids_needed))
        for res in results:
            res["path"] = path_map.get(str(res["pid"]), "unknown")

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:10]
