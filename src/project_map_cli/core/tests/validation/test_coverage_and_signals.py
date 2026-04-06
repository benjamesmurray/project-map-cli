import pytest
import sys
import subprocess
from pathlib import Path
from .fixtures import MultiLanguageRepo
from .verify import ShardLoader, SignalAssertions

def run_tool(root: Path, out_dir: Path) -> SignalAssertions:
    args = [
        sys.executable, "-m", "infra.digest_tool_v6",
        "--root", str(root),
        "--out-dir", str(out_dir),
        "--no-timestamp",
        "--profile", "full",
        "--ns-allow", "^.*$"
    ]
    proc = subprocess.run(args, capture_output=True, text=True)
    print(proc.stdout)
    print(proc.stderr)
    if proc.returncode != 0:
        proc.check_returncode()
    
    loader = ShardLoader(out_dir)
    routes = loader.get_shard("api.routes.json")
    if not routes or not routes.get("routes"):
        print("DEBUG: paths.json:", (out_dir / "paths.json").read_text())
        print("DEBUG: api.routes.json:", (out_dir / "api.routes.json").read_text())
        
    return SignalAssertions(loader)

def test_language_coverage(tmp_path):
    root = tmp_path / "repo"
    out = tmp_path / "out"
    repo = MultiLanguageRepo(root)
    repo.generate()
    
    verify = run_tool(root, out)
    
    # Python
    verify.assert_pydantic_model("User", ["id", "username"])
    verify.assert_route_exists("/api/v1/users", "GET", ["users"])
    
    # Kotlin
    verify.assert_kotlin_symbol("UserProcessor", "class")
    
    # SQL
    verify.assert_table_exists("users")

def test_cross_layer_signals(tmp_path):
    root = tmp_path / "repo"
    out = tmp_path / "out"
    repo = MultiLanguageRepo(root)
    repo.generate()
    
    verify = run_tool(root, out)
    
    # FE -> API
    verify.assert_fe_call_exists("/api/v1/users", "GET")
    
    # Check api_clients.map.json
    client_map = verify.loader.get_shard("api_clients.map.json")
    assert client_map, "api_clients.map.json missing"
    # Ensure there is a link between the FE call and the API route
    # The tool uses "route#METHOD" format for keys in api_clients.map.json
    found_link = False
    target = "/api/v1/users#GET"
    for link in client_map.get("links", []):
        if link["route"] == target:
            found_link = True
            break
    assert found_link, f"Link for {target} not found in api_clients.map.json"
