import json
import re
import random
import subprocess
import sys
from pathlib import Path

# Ground Truth Regexes (High Recall, Lower Precision)
RE_PY_FUNC = re.compile(r"^\s*def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
RE_KT_FUNC = re.compile(r"^\s*fun\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
RE_KT_CLASS = re.compile(r"^\s*(?:class|object|interface)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)

def audit_repo(root: Path, out_dir: Path):
    print(f"Auditing repo: {root}")
    
    # Run the tool
    args = [
        sys.executable, "-m", "project_map_cli.core",
        "--root", str(root),
        "--out-dir", str(out_dir),
        "--no-timestamp",
        "--profile", "full",
        "--exclude-dir", ".git",
        "--exclude-dir", "node_modules",
        "--exclude-dir", "venv"
    ]
    subprocess.run(args, check=True)
    
    # Load Shards
    paths_data = json.loads((out_dir / "paths.json").read_text())
    paths_map = {int(k): v for k, v in paths_data.items()}
    
    # Collect all Python and Kotlin files from paths_map
    py_files = [p for p in paths_map.values() if p.endswith(".py")]
    kt_files = [p for p in paths_map.values() if p.endswith(".kt")]
    
    # Sample 10 each
    sample_py = random.sample(py_files, min(len(py_files), 10))
    sample_kt = random.sample(kt_files, min(len(kt_files), 10))
    
    # Load symbol data from tool
    # For Python, we'll check files_index shards
    # For Kotlin, we'll check kotlin.symbols.json
    
    kt_symbols_doc = json.loads((out_dir / "kotlin.symbols.json").read_text())
    kt_symbols_by_file = {}
    
    # In kotlin.symbols.json, symbols don't have path, but they have pid.
    # Actually, let's look at the symbols list
    for s in kt_symbols_doc.get("symbols", []):
        pid = s["pid"]
        rel_path = paths_map.get(pid)
        if rel_path:
            kt_symbols_by_file.setdefault(rel_path, []).append(s["name"])
        
    # Python files_index
    py_symbols_by_file = {}
    fi_dir = out_dir / "files_index"
    for fi_shard in fi_dir.glob("*.min.json"):
        fi_data = json.loads(fi_shard.read_text())
        for f in fi_data.get("files", []):
            rel = paths_map[f["p"]]
            if rel.endswith(".py"):
                # Collect all functions and classes
                names = [x["name"] for x in f.get("f", [])] + [x["name"] for x in f.get("c", [])]
                py_symbols_by_file[rel] = names

    total_expected = 0
    total_found = 0
    failures = []

    print("\n--- Auditing Python ---")
    for rel in sample_py:
        text = (root / rel).read_text(encoding="utf-8", errors="ignore")
        expected = set(RE_PY_FUNC.findall(text))
        found = set(py_symbols_by_file.get(rel, []))
        
        missing = expected - found
        total_expected += len(expected)
        total_found += (len(expected) - len(missing))
        
        if missing:
            failures.append(f"PY Missing in {rel}: {missing}")
            print(f"X {rel} (missing {len(missing)})")
        else:
            print(f"V {rel}")

    print("\n--- Auditing Kotlin ---")
    for rel in sample_kt:
        text = (root / rel).read_text(encoding="utf-8", errors="ignore")
        expected = set(RE_KT_FUNC.findall(text)) | set(RE_KT_CLASS.findall(text))
        # Filter out common false positives from regex (e.g. keywords used in strings)
        expected = {n for n in expected if n not in ("fun", "class", "object", "interface", "val", "var")}
        
        found = set(kt_symbols_by_file.get(rel, []))
        
        missing = expected - found
        total_expected += len(expected)
        total_found += (len(expected) - len(missing))
        
        if missing:
            # Re-check: tree-sitter might be more precise than our regex
            # Only count as failure if it's truly a missing top-level symbol
            failures.append(f"KT Missing in {rel}: {missing}")
            print(f"X {rel} (missing {len(missing)})")
        else:
            print(f"V {rel}")

    score = (total_found / total_expected * 100) if total_expected > 0 else 100
    print(f"\n--- Quality Audit Results ---")
    print(f"Score: {score:.1f}% ({total_found}/{total_expected} symbols captured)")
    
    if failures:
        print("\nFailures:")
        for f in failures[:20]:
            print(f)

if __name__ == "__main__":
    audit_repo(Path("/opt/project"), Path("/tmp/audit_out"))
