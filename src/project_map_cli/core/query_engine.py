import json
import os
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Optional
import networkx as nx

class QueryEngine:
    def __init__(self, project_root: Optional[str] = None):
        self.project_root = project_root or os.environ.get("PROJECT_ROOT", os.getcwd())
        
        # Default index directory is relative to the project_root
        default_index = os.path.join(self.project_root, ".project-map", "docs", "repo_summary", "latest")
        index_dir = os.environ.get("PROJECT_MAP_DIR", default_index)
        self.digest_root = Path(index_dir)
        
        self.paths_cache: Optional[Dict[str, str]] = None
        self.metadata_cache: Optional[Dict[str, Any]] = None
        self.graph = nx.DiGraph()
        self.loaded_shards: Set[str] = set()
        self.current_digest_path: Optional[str] = None

    def get_latest_digest_path(self) -> str:
        try:
            real_path = self.digest_root.resolve(strict=True)
            if self.current_digest_path != str(real_path):
                self.paths_cache = None
                self.metadata_cache = None
                self.graph = nx.DiGraph()
                self.loaded_shards.clear()
                self.current_digest_path = str(real_path)
            return self.current_digest_path
        except FileNotFoundError:
            raise RuntimeError(f"Could not resolve digest path: {self.digest_root}. Ensure a scan has been run.")

    def read_json_shard(self, file_name: str) -> Any:
        latest_path = self.get_latest_digest_path()
        file_path = Path(latest_path) / file_name
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise RuntimeError(f"Failed to read shard {file_name}: {e}")

    def read_sharded_list(self, nav_key: str, list_key: str) -> List[Any]:
        """
        Reads one or more shards for a given nav_key and merges the lists found at list_key.
        """
        nav = self.read_json_shard("nav.json")
        val = nav.get(nav_key)
        if not val:
            return []
            
        file_names = val if isinstance(val, list) else [val]
        merged = []
        for fn in file_names:
            try:
                data = self.read_json_shard(fn)
                items = data.get(list_key, [])
                if isinstance(items, list):
                    merged.extend(items)
            except RuntimeError:
                continue
        return merged

    def get_metadata(self) -> Dict[str, Any]:
        if not self.metadata_cache:
            try:
                self.metadata_cache = self.read_json_shard("metadata.json")
            except RuntimeError:
                self.metadata_cache = {"version": "1.0.0", "status": "unknown"}
        return self.metadata_cache

    def _load_paths_cache(self) -> None:
        try:
            self.paths_cache = self.read_json_shard("paths.json")
        except RuntimeError:
            raise RuntimeError(f"Project map index not found at '{self.digest_root}'.\nPlease run 'project-map build' to generate it.")

    def resolve_pids(self, pids: List[str]) -> Dict[str, str]:
        if not self.paths_cache:
            self._load_paths_cache()

        result = {}
        for pid in pids:
            result[pid] = self.paths_cache.get(pid, "unknown")
        return result

    def get_pid_for_path(self, target_path: str) -> Optional[int]:
        if not self.paths_cache:
            self._load_paths_cache()

        try:
            # Normalize the path relative to project_root
            if Path(target_path).is_absolute():
                try:
                    rel_path = str(Path(target_path).relative_to(self.project_root))
                except ValueError:
                    rel_path = target_path
            else:
                rel_path = os.path.normpath(target_path)
        except ValueError:
            rel_path = target_path

        # Try exact match first
        for pid, path in self.paths_cache.items():
            if path == target_path or path == rel_path:
                return int(pid)
        
        # Fallback to suffix match
        for pid, path in self.paths_cache.items():
            if path.endswith(target_path) or path.endswith(rel_path):
                return int(pid)
        return None

    def _get_shard_key(self, target_path: str) -> str:
        # Simplistic version of orchestrator._top_level_pkg
        # Assumes target_path is relative to project_root if it's in the map
        parts = Path(target_path).parts
        if not parts:
            return "__root__"
        # If it's an absolute path, try to make it relative to project_root
        try:
            rel = Path(target_path).relative_to(self.project_root)
            parts = rel.parts
        except ValueError:
            pass
            
        if len(parts) <= 1:
            return "__root__"
        return parts[0]

    def get_file_outline(self, pid: int, target_path: str) -> Dict[str, Any]:
        shard_key = self._get_shard_key(target_path)
        try:
            shard_data = self.read_json_shard(f"files_index/{shard_key}.min.json")
            files = shard_data.get("files", [])
            for f in files:
                if f.get("p") == pid:
                    return f
        except RuntimeError:
            pass
        return {}

    def get_shallow_dependencies(self, pid: int, target_path: str) -> Dict[str, List[Dict[str, Any]]]:
        result = {"inbound": [], "outbound": []}
        
        # 1. Resolve module name for import matching
        try:
            p = Path(target_path)
            # Try to resolve relative to project_root if it's absolute
            if p.is_absolute():
                try:
                    rel_p = p.relative_to(Path(self.project_root).resolve())
                except ValueError:
                    rel_p = p
            else:
                rel_p = p
            
            # Remove extension
            mod_path = rel_p.with_suffix("")
            target_mod = ".".join(mod_path.parts)
        except Exception:
            target_mod = ""

        # 2. Check Python imports (analyzers.repo_only.json)
        try:
            imports_data = self.read_json_shard("analyzers.repo_only.json")
            edges = imports_data.get("edges", [])
            
            pids_to_resolve = set()
            for edge in edges:
                if edge.get("dst") == target_mod or edge.get("dst").startswith(target_mod + "."):
                    result["inbound"].append(edge)
                    pids_to_resolve.add(str(edge["pid"]))
                if edge.get("src") == target_mod or edge.get("pid") == pid:
                    result["outbound"].append(edge)
                    # For outbound, we don't have the dst PID directly in this shard, 
                    # usually it's just the module name.
            
            path_map = self.resolve_pids(list(pids_to_resolve))
            for edge in result["inbound"]:
                edge["path"] = path_map.get(str(edge["pid"]), "unknown")
                
        except RuntimeError:
            pass

        return result

    def search_symbols(self, query: str) -> List[Dict[str, Any]]:
        nav_keys = ["kotlin_symbols", "python_symbols", "typescript_symbols", "go_symbols", "rust_symbols"]
        all_matches = []
        q_lower = query.lower()

        # Try to find shards from nav.json, or fallback to defaults
        shards_to_read = []
        try:
            nav = self.read_json_shard("nav.json")
            for key in nav_keys:
                val = nav.get(key)
                if isinstance(val, list):
                    shards_to_read.extend(val)
                elif val:
                    shards_to_read.append(val)
        except RuntimeError:
            # Fallback to defaults
            shards_to_read = ["kotlin.symbols.json", "python.symbols.json", "typescript.symbols.json", "go.symbols.json", "rust.symbols.json"]

        for shard in shards_to_read:
            try:
                data = self.read_json_shard(shard)
                symbols = data.get("symbols", [])
                for s in symbols:
                    if (s.get("name", "").lower().find(q_lower) != -1 or 
                        s.get("qname", "").lower().find(q_lower) != -1):
                        all_matches.append(s)
            except RuntimeError:
                pass

        matches = all_matches[:100]
        pids = list({str(m["pid"]) for m in matches if m.get("pid") is not None})
        path_map = self.resolve_pids(pids) if pids else {}

        for m in matches:
            pid = m.get("pid")
            m["path"] = path_map.get(str(pid), "unknown") if pid is not None else "unknown"
        
        return matches

    def hydrate_neighborhood(self, fqn: str) -> None:
        meta = self.get_metadata()
        gsi = meta.get("gsi", meta.get("global_symbol_index", {}))
        shard_file = gsi.get(fqn)

        if not shard_file or shard_file in self.loaded_shards:
            return

        shard = self.read_json_shard(shard_file)
        edges = shard.get("edges", {})

        for src, target_edges in edges.items():
            if not self.graph.has_node(src):
                self.graph.add_node(src)
            for edge in target_edges:
                dst = edge["dst"]
                if not self.graph.has_node(dst):
                    self.graph.add_node(dst)
                if not self.graph.has_edge(src, dst):
                    self.graph.add_edge(src, dst, pid=edge.get("pid"), ln=edge.get("ln"))

        self.loaded_shards.add(shard_file)

    def get_callers(self, target_fqn: str) -> List[Dict[str, Any]]:
        self.hydrate_neighborhood(target_fqn)
        if not self.graph.has_node(target_fqn):
            return []
        
        result = []
        for caller in self.graph.predecessors(target_fqn):
            edge_data = self.graph.get_edge_data(caller, target_fqn)
            pid = edge_data.get("pid")
            ln = edge_data.get("ln")
            path = self.resolve_pids([str(pid)]).get(str(pid), "unknown") if pid is not None else "unknown"
            result.append({"fqn": caller, "pid": pid, "ln": ln, "path": path})
        return result

    def analyze_impact(self, target_fqn: str, fanout_cap: int = 50) -> Dict[str, Any]:
        impact_graph = nx.DiGraph()
        queue = [target_fqn]
        visited = set()
        count = 0

        try:
            kafka_data = self.read_json_shard("kafka.streams_eda.json")
        except RuntimeError:
            kafka_data = {}

        while queue and count < fanout_cap:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            count += 1

            if not impact_graph.has_node(current):
                impact_graph.add_node(current)

            callers = self.get_callers(current)
            for caller in callers:
                c_fqn = caller["fqn"]
                if not impact_graph.has_node(c_fqn):
                    impact_graph.add_node(c_fqn)
                impact_graph.add_edge(c_fqn, current, type="call", **caller)
                queue.append(c_fqn)

        # Simplified impact analysis returning nodes count
        return {
            "target": target_fqn,
            "impacted_nodes_count": len(visited),
            "reached_cap": count >= fanout_cap
        }
