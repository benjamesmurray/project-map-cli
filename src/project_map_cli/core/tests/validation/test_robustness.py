import pytest
import sys
import subprocess
from pathlib import Path
from .fixtures import EdgeCaseRepo, MultiLanguageRepo
from .test_coverage_and_signals import run_tool
from .verify import ShardLoader, SignalAssertions

def test_robustness(tmp_path):
    root = tmp_path / "repo"
    out = tmp_path / "out"
    repo = EdgeCaseRepo(root)
    repo.generate()
    
    # Tool should not crash on empty files or syntax errors
    verify = run_tool(root, out)
    
    # The tool shards by top-level directory. large.py is in root, so it goes to __root__.min.json
    fi_shard = out / "files_index" / "__root__.min.json"
    import json
    data = json.loads(fi_shard.read_text())
    
    large_rec = next(f for f in data["files"] if "large.py" in verify.loader.paths_map[f["p"]])
    assert len(large_rec["f"]) == 1000

def test_determinism(tmp_path):
    root = tmp_path / "repo"
    repo = MultiLanguageRepo(root)
    repo.generate()
    
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    
    run_tool(root, out1)
    run_tool(root, out2)
    
    # Compare all files except run_id in nav.json
    def get_files(d):
        return sorted([p.relative_to(d) for p in d.rglob("*") if p.is_file()])
    
    files1 = get_files(out1)
    files2 = get_files(out2)
    assert files1 == files2
    
    for rel in files1:
        p1 = out1 / rel
        p2 = out2 / rel
        if rel.name == "nav.json":
            import json
            j1 = json.loads(p1.read_text())
            j2 = json.loads(p2.read_text())
            del j1["run_id"]
            del j2["run_id"]
            assert j1 == j2
        else:
            assert p1.read_bytes() == p2.read_bytes(), f"Determinism failure in {rel}"
