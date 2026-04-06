import json
import os
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Optional
import networkx as nx

class QueryEngine:
    def __init__(self, wde_root: Optional[str] = None):
        self.wde_root = wde_root or os.environ.get("WDE_ROOT", "/opt/wde")
        self.digest_root = Path(self.wde_root) / "docs/repo_summary/latest"
        
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

    def get_metadata(self) -> Dict[str, Any]:
        if not self.metadata_cache:
            self.metadata_cache = self.read_json_shard("metadata.json")
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
        shards = ["kotlin.symbols.json", "python.symbols.json", "typescript.symbols.json"]
        all_matches = []
        q_lower = query.lower()

        for shard in shards:
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
