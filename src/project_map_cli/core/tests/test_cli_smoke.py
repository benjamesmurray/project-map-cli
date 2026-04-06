# tests/digest_tool_v6/test_cli_smoke.py
from __future__ import annotations

import json
import os
import sqlite3
import sys
import textwrap
from pathlib import Path
from typing import Dict, List, Tuple
import subprocess


# ------------------------- helpers -------------------------

def _write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(s).lstrip("\n"), encoding="utf-8")


def _count_loc_nonempty(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.strip())


def _run_cli(root: Path, out_dir: Path, extra_args: List[str] | None = None) -> subprocess.CompletedProcess:
    args = [
        sys.executable, "-m", "infra.digest_tool_v6",
        "--root", str(root),
        "--out-dir", str(out_dir),
        "--no-timestamp",
        # No --ns-allow → auto-infer should kick in
        "--max-callsites", "3",
        "--max-hotspots", "5",
        "--max-entry-points", "5",
        "--max-top-symbols", "5",
        "--max-shard-mb", "2",
    ]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _build_fixture_repo(tmp_path: Path) -> Path:
    """
    Creates a tiny repo:
      mypkg/
        __init__.py
        core.py           (class + function)
        api.py            (FastAPI app + route)
      web/
        SceneView.vue     (axios GET)
      db/
        demo.sqlite       (1 table)
    """
    root = tmp_path / "repo"
    # Python package
    _write(root / "mypkg/__init__.py", """# package\n""")
    _write(root / "mypkg/core.py", """
        class Greeter:
            def __init__(self, name: str):  # noqa: D401
                self.name = name

            def greet(self) -> str:
                \"\"\"Say hi\"\"\"
                return f"hi {self.name}"

        def helper(x: int) -> int:
            return x + 1

        if __name__ == "__main__":
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--name")
    """)
    _write(root / "mypkg/api.py", """
        from fastapi import FastAPI, Depends

        app = FastAPI()

        def auth_dep():
            return True

        @app.get("/api/scene", tags=["scene"], dependencies=[Depends(auth_dep)])
        def get_scene():
            return {"ok": True}
    """)

    # Vue component with axios + fetch
    _write(root / "web/SceneView.vue", """
        <template><div>Scene</div></template>
        <script setup>
        import axios from "axios"
        const r = await axios.get('/api/scene', { params: { symbol: 'X' } })
        const r2 = await fetch(`/api/scene`, { method: 'GET', headers: { 'X': 'y' } })
        </script>
    """)

    # SQLite db
    dbp = root / "db/demo.sqlite"
    dbp.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(dbp) as conn:
        conn.execute("create table if not exists items(id integer primary key, name text not null)")
        conn.commit()

    return root


def _load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _collect_out_files(out_dir: Path) -> List[Path]:
    return sorted([p for p in out_dir.rglob("*") if p.is_file()], key=lambda p: p.as_posix())


def _compare_files_excluding_run_id(dir1: Path, dir2: Path) -> Tuple[bool, List[str]]:
    """
    Compare all shard bytes between dir1 and dir2.
    If nav.json differs only by run_id, ignore it. Return (equal, diffs).
    """
    files1 = {p.relative_to(dir1).as_posix(): p for p in _collect_out_files(dir1)}
    files2 = {p.relative_to(dir2).as_posix(): p for p in _collect_out_files(dir2)}
    diffs: List[str] = []

    if files1.keys() != files2.keys():
        diffs.append(f"file sets differ: {sorted(files1.keys())} vs {sorted(files2.keys())}")
        return False, diffs

    for rel, p1 in files1.items():
        p2 = files2[rel]
        if rel == "nav.json":
            j1 = _load_json(p1)
            j2 = _load_json(p2)
            j1_wo = {k: v for k, v in j1.items() if k != "run_id"}
            j2_wo = {k: v for k, v in j2.items() if k != "run_id"}
            if j1_wo != j2_wo:
                diffs.append(f"nav.json differs (excluding run_id)")
        else:
            b1 = p1.read_bytes()
            b2 = p2.read_bytes()
            if b1 != b2:
                diffs.append(f"bytes differ in {rel}")
    return (len(diffs) == 0), diffs


# ------------------------- tests -------------------------

def test_cli_e2e_and_presence(tmp_path: Path) -> None:
    root = _build_fixture_repo(tmp_path)
    out_dir = tmp_path / "out1"

    proc = _run_cli(root, out_dir)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"

    # Required shards / dirs
    expected_files = {
        "nav.json",
        "paths.json",
        "digest.top.json",
        "analyzers.repo_only.json",
        "ctor.top.json",
        "api.routes.json",
        "pydantic.models.json",
        "fe.calls.json",
        "db.schema.json",
        "api_clients.map.json",
    }
    expected_dirs = {"ctor.items", "files_index"}

    existing = {p.name for p in out_dir.iterdir() if p.is_file()}
    existing_dirs = {p.name for p in out_dir.iterdir() if p.is_dir()}
    assert expected_files.issubset(existing), f"missing shards: {expected_files - existing}"
    assert expected_dirs.issubset(existing_dirs), f"missing dirs: {expected_dirs - existing_dirs}"

    # files_index shards should exist and be non-empty
    fi_dir = out_dir / "files_index"
    fi_files = [p for p in fi_dir.glob("*.min.json")]
    assert fi_files, "no files_index shards emitted"

    # api.routes should have our GET /api/scene
    routes = _load_json(out_dir / "api.routes.json")["routes"]
    assert any(r["path"] == "/api/scene" and r["method"] == "GET" for r in routes)


def test_files_index_compression_round_trip(tmp_path: Path) -> None:
    root = _build_fixture_repo(tmp_path)
    out_dir = tmp_path / "out2"

    proc = _run_cli(root, out_dir)
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}"

    # Load paths map
    paths = {int(k): v for k, v in _load_json(out_dir / "paths.json").items()}

    # Pick one files_index shard (any)
    fi_file = next((out_dir / "files_index").glob("*.min.json"))
    fi = _load_json(fi_file)
    assert "files" in fi and fi["files"], "files_index shard empty"

    # For each file record, re-derive core counts and compare
    for rec in fi["files"]:
        pid = rec["p"]
        rel = paths[pid]
        p = (Path(root) / rel)
        src = p.read_text(encoding="utf-8", errors="replace")
        # LOC
        assert rec["l"] == _count_loc_nonempty(src)
        # Imports/classes/functions/methods presence & basic shape
        for key in ("i", "c", "f", "m"):
            assert key in rec and isinstance(rec[key], list)
        # Docstring flag is deterministic; we only assert type here to avoid fp due to shebang/encoding lines
        assert rec["d"] in (0, 1)


def test_determinism_two_runs(tmp_path: Path) -> None:
    root = _build_fixture_repo(tmp_path)
    out1 = tmp_path / "out_det_1"
    out2 = tmp_path / "out_det_2"

    # first run
    p1 = _run_cli(root, out1)
    assert p1.returncode == 0, f"first run failed: {p1.stderr}"
    # second run (identical)
    p2 = _run_cli(root, out2)
    assert p2.returncode == 0, f"second run failed: {p2.stderr}"

    equal, diffs = _compare_files_excluding_run_id(out1, out2)
    assert equal, "Outputs differ between runs (excluding nav.run_id): " + "; ".join(diffs)
