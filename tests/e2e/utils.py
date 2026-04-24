import os
import sys
import subprocess
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

def get_project_map_bin() -> List[str]:
    """Returns the command to run project-map."""
    bin_env = os.environ.get("PROJECT_MAP_BIN")
    if bin_env:
        return bin_env.split()
    # Fallback to current python module
    return [sys.executable, "-m", "project_map_cli.core"]

def run_project_map(root: Path, out_dir: Path, extra_args: Optional[List[str]] = None) -> subprocess.CompletedProcess:
    """Executes the project-map CLI."""
    cmd = get_project_map_bin() + [
        "--root", str(root),
        "--out-dir", str(out_dir),
        "--no-timestamp"
    ]
    if extra_args:
        cmd.extend(extra_args)
    
    return subprocess.run(cmd, capture_output=True, text=True)

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

class SchemaValidator:
    def __init__(self, loader: ShardLoader):
        self.loader = loader

    def assert_shard_shape(self, name: str, expected_keys: List[str]):
        data = self.loader.get_shard(name)
        assert data is not None, f"Shard {name} missing"
        for key in expected_keys:
            assert key in data, f"Key '{key}' missing in {name}"

    def assert_list_item_shape(self, name: str, list_key: str, expected_keys: List[str]):
        data = self.loader.get_shard(name)
        assert data is not None, f"Shard {name} missing"
        items = data.get(list_key, [])
        assert isinstance(items, list), f"'{list_key}' in {name} is not a list"
        if items:
            for key in expected_keys:
                assert key in items[0], f"Key '{key}' missing in items of {list_key} in {name}"
