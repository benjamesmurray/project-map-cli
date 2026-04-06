import json
import os
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Optional
import networkx as nx

class QueryEngine:
    def __init__(self, project_root: Optional[str] = None):
        self.project_root = project_root or os.environ.get("PROJECT_ROOT", "/opt/project")
        self.digest_root = Path(self.project_root) / "docs/repo_summary/latest"
        
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

    def resolve_pids(self, pids: List[str]) -> Dict[str, str]:
        if not self.paths_cache:
            try:
                self.paths_cache = self.read_json_shard("paths.json")
            except RuntimeError:
                self.paths_cache = {}

        result = {}
        for pid in pids:
            result[pid] = self.paths_cache.get(pid, "unknown")
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
