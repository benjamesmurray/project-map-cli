import pytest
from pathlib import Path
from .utils import run_project_map, ShardLoader, SchemaValidator

@pytest.fixture
def fixtures_root():
    return Path(__file__).parent.parent / "fixtures" / "repositories"

def test_gradle_analyzer(tmp_path, fixtures_root):
    repo_root = fixtures_root / "gradle_repo"
    out_dir = tmp_path / "out_gradle"
    
    result = run_project_map(repo_root, out_dir, ["--profile", "full"])
    assert result.returncode == 0
    
    loader = ShardLoader(out_dir)
    validator = SchemaValidator(loader)
    
    validator.assert_shard_shape("gradle.modules.json", ["modules", "settings"])
    gradle_data = loader.get_shard("gradle.modules.json")
    
    assert gradle_data["settings"]["modules_count"] == 2
    modules = {m["name"]: m for m in gradle_data["modules"]}
    assert ":app" in modules
    assert ":lib" in modules
    assert modules[":app"]["kafka"] is True

def test_kotlin_inheritance(tmp_path, fixtures_root):
    repo_root = fixtures_root / "kotlin_inheritance"
    out_dir = tmp_path / "out_kt_inh"
    
    result = run_project_map(repo_root, out_dir, ["--profile", "full"])
    assert result.returncode == 0
    
    loader = ShardLoader(out_dir)
    validator = SchemaValidator(loader)
    
    validator.assert_shard_shape("inheritance.json", ["edges", "metadata"])
    inheritance_data = loader.get_shard("inheritance.json")
    
    # Check for SubProcessor -> BaseProcessor link
    assert "com.example.SubProcessor" in inheritance_data["edges"]
    targets = [e["dst"] for e in inheritance_data["edges"]["com.example.SubProcessor"]]
    assert "com.example.BaseProcessor" in targets

def test_ts_symbols(tmp_path, fixtures_root):
    repo_root = fixtures_root / "ts_symbols"
    out_dir = tmp_path / "out_ts"
    
    result = run_project_map(repo_root, out_dir, ["--profile", "full"])
    assert result.returncode == 0
    
    loader = ShardLoader(out_dir)
    validator = SchemaValidator(loader)
    
    validator.assert_shard_shape("typescript.symbols.json", ["symbols"])
    ts_symbols = loader.get_shard("typescript.symbols.json")["symbols"]
    
    # Check for D3 chains
    render_method = next(s for s in ts_symbols if s["name"] == "render")
    assert "external_patterns" in render_method
    d3_pattern = next(p for p in render_method["external_patterns"] if p["library"] == "d3")
    assert "selectAll" in d3_pattern["chain"]
    assert "append" in d3_pattern["chain"]
