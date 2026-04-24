import pytest
from pathlib import Path
from .utils import run_project_map, ShardLoader, SchemaValidator

@pytest.fixture
def repo_root():
    return Path(__file__).parent.parent / "fixtures" / "repositories" / "full_stack_app"

def test_full_stack_e2e(tmp_path, repo_root):
    out_dir = tmp_path / "out"
    
    # Run the tool
    result = run_project_map(repo_root, out_dir, ["--profile", "full"])
    assert result.returncode == 0, f"Tool failed with stderr: {result.stderr}"
    
    loader = ShardLoader(out_dir)
    validator = SchemaValidator(loader)
    
    # 1. Validate Shard Existence & Basic Shape
    shards_to_check = {
        "api.routes.json": ["routes"],
        "pydantic.models.json": ["models"],
        "fe.calls.json": ["calls"],
        "db.schema.json": ["tables"],
        "kafka.streams_eda.json": ["topics", "edges"],
        "api_clients.map.json": ["links"],
        "paths.json": []
    }
    
    for shard, keys in shards_to_check.items():
        validator.assert_shard_shape(shard, keys)
    
    # 2. Validate Specific List Item Shapes
    validator.assert_list_item_shape("api.routes.json", "routes", ["path", "method", "tags"])
    validator.assert_list_item_shape("pydantic.models.json", "models", ["name", "fields"])
    validator.assert_list_item_shape("db.schema.json", "tables", ["name", "columns"])
    
    # 3. Validate Signals (Cross-layer links)
    client_map = loader.get_shard("api_clients.map.json")
    # Verify we have at least one link between FE and API
    assert len(client_map["links"]) > 0
    found_route_link = any(link["route"] == "/api/v1/users#GET" for link in client_map["links"])
    assert found_route_link, "Expected link to /api/v1/users#GET not found in api_clients.map.json"

def test_multi_lang_e2e(tmp_path):
    repo_root = Path(__file__).parent.parent / "fixtures" / "repositories" / "multi_lang"
    out_dir = tmp_path / "out_multi"
    
    result = run_project_map(repo_root, out_dir, ["--profile", "full"])
    assert result.returncode == 0
    
    loader = ShardLoader(out_dir)
    validator = SchemaValidator(loader)
    
    # Go symbols
    validator.assert_shard_shape("go.symbols.json", ["symbols"])
    go_symbols = loader.get_shard("go.symbols.json")["symbols"]
    assert any(s["name"] == "Greeter" and s["kind"] == "type" for s in go_symbols)
    
    # Rust symbols
    validator.assert_shard_shape("rust.symbols.json", ["symbols"])
    rust_symbols = loader.get_shard("rust.symbols.json")["symbols"]
    assert any(s["name"] == "Rectangle" and s["kind"] == "struct" for s in rust_symbols)
