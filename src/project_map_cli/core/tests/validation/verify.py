from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

class ShardLoader:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.cache: Dict[str, Any] = {}
        self.paths_map: Dict[int, str] = self._load_paths()

    def _load_paths(self) -> Dict[int, str]:
        p = self.out_dir / "paths.json"
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return {int(k): v for k, v in data.items()}

    def get_shard(self, name: str) -> Any:
        if name in self.cache:
            return self.cache[name]
        p = self.out_dir / name
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        self.cache[name] = data
        return data

class SignalAssertions:
    def __init__(self, loader: ShardLoader):
        self.loader = loader

    def assert_route_exists(self, path: str, method: str, tags: Optional[List[str]] = None):
        routes = self.loader.get_shard("api.routes.json")
        assert routes, "api.routes.json missing"
        found = False
        for r in routes.get("routes", []):
            if r["path"] == path and r["method"] == method:
                if tags:
                    assert set(tags).issubset(set(r.get("tags", [])))
                found = True
                break
        assert found, f"Route {method} {path} not found"

    def assert_fe_call_exists(self, url: str, method: str):
        client_map = self.loader.get_shard("api_clients.map.json")
        assert client_map, "api_clients.map.json missing"
        found = False
        target = f"{url}#{method}"
        for link in client_map.get("links", []):
            if link["route"] == target:
                found = True
                break
        assert found, f"FE call {method} {url} not found in api_clients.map.json"

    def assert_kafka_topic_exists(self, topic: str, role: str):
        # role: 'producer' or 'consumer'
        eda = self.loader.get_shard("kafka.streams_eda.json")
        assert eda, "kafka.streams_eda.json missing"
        found = False
        # Check topics list
        for t in eda.get("topics", []):
            if t.get("topic") == topic:
                # In this tool, 'stream'/'table' are consumers, 'to'/'through' (in edges) are producers
                if role == "consumer" and t.get("op") in ("stream", "table"):
                    found = True
                    break
        
        if not found and role == "producer":
            # Check edges for producers
            for e in eda.get("edges", []):
                if e.get("to") == topic and e.get("edge_kind") == "dsl":
                    found = True
                    break
                    
        assert found, f"Kafka topic {topic} as {role} not found"

    def assert_pydantic_model(self, name: str, fields: List[str]):
        models = self.loader.get_shard("pydantic.models.json")
        assert models, "pydantic.models.json missing"
        found = False
        for m in models.get("models", []):
            if m["name"] == name:
                existing_fields = [f["name"] for f in m.get("fields", [])]
                assert set(fields).issubset(set(existing_fields))
                found = True
                break
        assert found, f"Pydantic model {name} not found"

    def assert_table_exists(self, table_name: str):
        db = self.loader.get_shard("db.schema.json")
        assert db, "db.schema.json missing"
        found = any(t["name"] == table_name for t in db.get("tables", []))
        assert found, f"SQL table {table_name} not found"

    def assert_kotlin_symbol(self, name: str, kind: str):
        symbols = self.loader.get_shard("kotlin.symbols.json")
        assert symbols, "kotlin.symbols.json missing"
        found = False
        for s in symbols.get("symbols", []):
            if s["name"] == name and s["kind"] == kind:
                found = True
                break
        assert found, f"Kotlin symbol {name} ({kind}) not found"
