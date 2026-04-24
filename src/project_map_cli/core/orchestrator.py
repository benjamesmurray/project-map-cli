# utils/digest_tool_v3/orchestrator.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

from .config import Config
from .common import fs_scan, pid_registry, write, hashing
from .common import ast_utils

from .analyzers import (
    digest_top,
    imports_repo_only,
    ctor_signatures,
    files_index,
    fastapi_routes,
    pydantic_models,
    fe_calls,
    db_schema,
    api_clients_map,
    # v3
    gradle_modules,
    kotlin_symbols,
    kafka_streams_eda,
    # v5
    symbol_registry,
    kotlin_calls,
    inheritance,
    # v6
    python_symbols,
    typescript_symbols,
    vue_analyzer,
    # Go/Rust
    go_symbols,
    rust_symbols,
)
from .common import graph_serialization
from .bundle import make_all as make_bundle


def _ensure_dirs(out_dir: Path, make_ctor: bool, make_files_index: bool) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dirs = {"root": out_dir}
    if make_ctor:
        p = out_dir / "ctor.items"
        p.mkdir(parents=True, exist_ok=True)
        dirs["ctor_items"] = p
    if make_files_index:
        p = out_dir / "files_index"
        p.mkdir(parents=True, exist_ok=True)
        dirs["files_index"] = p
    return dirs


def _write_nav(
    out_dir: Path,
    run_id: str,
    filenames: Dict[str, str | List[str]],
    include_ctor: bool,
    include_fi: bool,
    max_bytes: int,
) -> None:
    nav_doc = {
        "run_id": run_id,
        "digest": filenames.get("digest.top.json"),
        "analyzers": filenames.get("analyzers.repo_only.json"),
        "ctor_top": filenames.get("ctor.top.json"),
        "ctor_items_root": "ctor.items/" if include_ctor else None,
        "files_index_root": "files_index/" if include_fi else None,
        "paths_map": filenames.get("paths.json"),
        "api_routes": filenames.get("api.routes.json"),
        "pydantic_models": filenames.get("pydantic.models.json"),
        "fe_calls": filenames.get("fe.calls.json"),
        "db_schema": filenames.get("db.schema.json"),
        "api_clients_map": filenames.get("api_clients.map.json"),
        # v3 additions
        "gradle_modules": filenames.get("gradle.modules.json"),
        "kotlin_symbols": filenames.get("kotlin.symbols.json"),
        "kafka_streams_eda": filenames.get("kafka.streams_eda.json"),
        # v5 additions
        "kotlin_calls": filenames.get("kotlin_calls.json"),
        "inheritance": filenames.get("inheritance.json"),
        "metadata": filenames.get("metadata.json"),
        # v6 additions
        "python_symbols": filenames.get("python.symbols.json"),
        "typescript_symbols": filenames.get("typescript.symbols.json"),
        "vue_symbols": filenames.get("vue.symbols.json"),
        # Go/Rust
        "go_symbols": filenames.get("go.symbols.json"),
        "rust_symbols": filenames.get("rust.symbols.json"),
    }
    write.write_json(out_dir / "nav.json", nav_doc, max_bytes=max_bytes)


def _kotlin_hot_files(cfg: Config, kt_files: List[Path]) -> List[Path]:
    """
    Light profile: only parse Kotlin symbols for 'hot' files.

    Hot heuristic (cheap string scan):
      - imports org.apache.kafka.streams...
      - contains `.stream(` (Kafka Streams DSL)
    """
    hot: List[Path] = []
    for p in kt_files:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if cfg.kotlin_hot_import_re.search(txt) or cfg.kotlin_hot_usage_re.search(txt):
            hot.append(p)
    return hot


def run(cfg: Config) -> None:
    start_time = datetime.now(timezone.utc)
    # Decide final output directory (timestamped subfolder if requested)
    if cfg.timestamped_out:
        ts = start_time.strftime("%Y%m%d_%H%M%S")
        final_out = cfg.out_dir / ts
    else:
        final_out = cfg.out_dir

    include_files_index = cfg.profile != "light"
    include_ctor_items = cfg.profile != "light"

    dirs = _ensure_dirs(final_out, make_ctor=include_ctor_items, make_files_index=include_files_index)

    scan = fs_scan.scan(cfg)
    py_files: List[Path] = scan["py_files"]
    vue_files: List[Path] = scan["vue_files"]  # kept for completeness
    fe_files: List[Path] = scan.get("fe_files", vue_files)
    sql_files: List[Path] = scan["sql_files"]
    sqlite_files: List[Path] = scan["sqlite_files"]
    all_files: List[Path] = scan["all_files"]

    # v3 buckets
    kt_files: List[Path] = scan.get("kt_files", [])
    gradle_files: List[Path] = scan.get("gradle_files", [])
    config_files: List[Path] = scan.get("config_files", [])

    # v6/new buckets
    go_files: List[Path] = scan.get("go_files", [])
    rs_files: List[Path] = scan.get("rs_files", [])

    # Enforce profile-based caps early (cheap)
    # Kotlin symbols scope:
    # - light: hot only
    # - full: all
    if cfg.kotlin_symbols_scope == "hot":
        kt_files_for_symbols = _kotlin_hot_files(cfg, kt_files)
    else:
        kt_files_for_symbols = list(kt_files)

    # Apply max_kotlin_files cap (deterministic order already)
    if cfg.max_kotlin_files and len(kt_files_for_symbols) > cfg.max_kotlin_files:
        kt_files_for_symbols = kt_files_for_symbols[: cfg.max_kotlin_files]

    # For Kafka Streams EDA we generally want "hot" too, but in full mode you may want wider scan.
    # We'll follow the same cap and let the analyzer decide how to use it.
    kt_files_for_eda = list(kt_files_for_symbols)

    # Auto-infer namespace allow-list if not provided
    if cfg.ns_allow_re is None and cfg.ns_auto:
        tops: List[str] = []
        for p in py_files:
            mod = ast_utils.module_name_from_path(p, cfg.root)
            if not mod:
                continue
            parts = mod.split(".")
            top = parts[0]
            if top in {"tests", "test", "testing", "__root__"}:
                continue
            if top not in tops:
                tops.append(top)
            # If top level is 'src' or 'lib', also allow the next level as a top level
            if top in {"src", "lib"} and len(parts) > 1:
                sub = parts[1]
                if sub not in tops:
                    tops.append(sub)
        if tops:
            pattern = r"^(" + "|".join(sorted(tops)) + r")(\.|$)"
        else:
            pattern = r"^[A-Za-z_][A-Za-z0-9_]*(\.|$)"

        # Rebuild cfg with inferred allow-list, preserving all other settings
        cfg = Config(
            root=cfg.root,
            out_dir=cfg.out_dir,
            ns_allow=pattern,
            ns_auto=cfg.ns_auto,
            timestamped_out=cfg.timestamped_out,
            bundle_all=cfg.bundle_all,
            bundle_gzip=cfg.bundle_gzip,
            profile=cfg.profile,
            db_url_env=cfg.db_url_env,
            exclude_dirs=cfg.exclude_dirs,
            exclude_files_exact=cfg.exclude_files_exact,
            exclude_globs=cfg.exclude_globs,
            excludes=cfg.excludes,
            max_callsites=cfg.max_callsites,
            max_hotspots=cfg.max_hotspots,
            max_entry_points=cfg.max_entry_points,
            max_top_symbols=cfg.max_top_symbols,
            max_shard_mb=cfg.max_shard_mb,
            max_kotlin_files=cfg.max_kotlin_files,
            max_kotlin_symbols_per_file=cfg.max_kotlin_symbols_per_file,
            max_topics=cfg.max_topics,
            max_edges=cfg.max_edges,
        )
        # dirs already created above; continue

    pid_by_path, paths_map = pid_registry.assign(all_files)
    paths_json = {str(pid): str(path) for pid, path in sorted(paths_map.items(), key=lambda kv: kv[0])}
    write.write_json(final_out / "paths.json", paths_json, max_bytes=cfg.max_shard_bytes)

    run_id = f"run_{hashing.stable_hash(paths_json)[7:19]}"

    filenames: Dict[str, str | List[str]] = {
        "paths.json": "paths.json",
        "digest.top.json": "digest.top.json",
        "analyzers.repo_only.json": "analyzers.repo_only.json",
        "ctor.top.json": "ctor.top.json",
        "api.routes.json": "api.routes.json",
        "pydantic.models.json": "pydantic.models.json",
        "fe.calls.json": "fe.calls.json",
        "db.schema.json": "db.schema.json",
        "api_clients.map.json": "api_clients.map.json",
        # v3 additions
        "gradle.modules.json": "gradle.modules.json",
        "kotlin.symbols.json": "kotlin.symbols.json",
        "kafka.streams_eda.json": "kafka.streams_eda.json",
        # v5 additions
        "kotlin_calls.json": "kotlin_calls.json",
        "inheritance.json": "inheritance.json",
        "metadata.json": "metadata.json",
        # v6 additions
        "python.symbols.json": "python.symbols.json",
        "typescript.symbols.json": "typescript.symbols.json",
        "vue.symbols.json": "vue.symbols.json",
        # Go/Rust
        "go.symbols.json": "go.symbols.json",
        "rust.symbols.json": "rust.symbols.json",
    }

    errors: List[Dict[str, str]] = []

    def safe_analyze(name, analyzer_func, *args, **kwargs):
        try:
            return analyzer_func(*args, **kwargs)
        except Exception as exc:
            msg = f"Analyzer '{name}' failed: {exc}"
            print(f"[digest_tool_v3] ERROR: {msg}")
            errors.append({"analyzer": name, "error": str(exc)})
            return None

    # Pre-initialize all shard files with minimal valid JSON
    for fname in [fn for fn in filenames.values() if isinstance(fn, str)]:
        fpath = final_out / fname
        if fname == "db.schema.json":
            write.write_json(fpath, {"tables": [], "orm_models": []}, max_bytes=cfg.max_shard_bytes)
        elif fname in ("api_clients.map.json", "kafka.streams_eda.json"):
            write.write_json(fpath, {"links": [], "topics": [], "edges": []}, max_bytes=cfg.max_shard_bytes)
        elif fname in ("digest.top.json", "analyzers.repo_only.json", "ctor.top.json"):
            write.write_json(fpath, {}, max_bytes=cfg.max_shard_bytes)
        elif fname == "metadata.json":
            write.write_json(fpath, {"version": "1.0.0", "capabilities": [], "gsi": {}}, max_bytes=cfg.max_shard_bytes)
        elif fname == "paths.json":
            pass # already written
        else:
            write.write_json(fpath, {"routes": [], "models": [], "calls": [], "symbols": [], "modules": []}, max_bytes=cfg.max_shard_bytes)

    # files_index (sharded) — only in full profile
    fi_stats: Dict[str, Any] = {}
    if include_files_index:
        fi_result = safe_analyze("files_index", files_index.analyze, cfg, py_files, pid_by_path)
        if fi_result:
            fi_stats = fi_result.get("stats", {})
            for shard_name, shard_doc in fi_result["shards"].items():
                write.write_json(
                    dirs["files_index"] / f"{shard_name}.min.json",
                    shard_doc,
                    max_bytes=cfg.max_shard_bytes,
                )

    # repo-only import graph
    imports_doc = safe_analyze("imports_repo_only", imports_repo_only.analyze, cfg, py_files, pid_by_path)
    if imports_doc:
        write.write_json(final_out / "analyzers.repo_only.json", imports_doc, max_bytes=cfg.max_shard_bytes)

    # ctor signatures: top + items (items only in full profile)
    ctor_res = safe_analyze("ctor_signatures", ctor_signatures.analyze, cfg, py_files, pid_by_path)
    if ctor_res:
        ctor_top_doc, ctor_items = ctor_res
        write.write_json(final_out / "ctor.top.json", ctor_top_doc, max_bytes=cfg.max_shard_bytes)
        if include_ctor_items:
            for module_name, item_doc in ctor_items.items():
                write.write_json(
                    dirs["ctor_items"] / f"{module_name}.json",
                    item_doc,
                    max_bytes=cfg.max_shard_bytes,
                )

    # digest.top.json (depends on fi_stats + imports_doc)
    if fi_stats and imports_doc:
        digest_doc = safe_analyze("digest_top", digest_top.analyze, cfg=cfg, files_index_stats=fi_stats, imports_doc=imports_doc)
        if digest_doc:
            write.write_json(final_out / "digest.top.json", digest_doc, max_bytes=cfg.max_shard_bytes)

    # FastAPI routes
    api_routes_doc = safe_analyze("fastapi_routes", fastapi_routes.analyze, cfg, py_files, pid_by_path)
    if api_routes_doc:
        write.write_json(final_out / "api.routes.json", api_routes_doc, max_bytes=cfg.max_shard_bytes)
    else:
        api_routes_doc = {"routes": []}

    # Pydantic models
    pydantic_doc = safe_analyze("pydantic_models", pydantic_models.analyze, cfg, py_files, pid_by_path)
    if pydantic_doc:
        write.write_json(final_out / "pydantic.models.json", pydantic_doc, max_bytes=cfg.max_shard_bytes)

    # Frontend calls
    fe_calls_doc = safe_analyze("fe_calls", fe_calls.analyze, cfg, fe_files, pid_by_path)
    if fe_calls_doc:
        write.write_json(final_out / "fe.calls.json", fe_calls_doc, max_bytes=cfg.max_shard_bytes)
    else:
        fe_calls_doc = {"calls": []}

    # Database schema
    db_schema_doc = safe_analyze("db_schema", db_schema.analyze, cfg, py_files, sql_files, sqlite_files, pid_by_path)
    if db_schema_doc:
        write.write_json(final_out / "db.schema.json", db_schema_doc, max_bytes=cfg.max_shard_bytes)

    # FE↔API link map
    if api_routes_doc and fe_calls_doc:
        api_clients_map_doc = safe_analyze("api_clients_map", api_clients_map.analyze, cfg, api_routes_doc, fe_calls_doc)
        if api_clients_map_doc:
            write.write_json(final_out / "api_clients.map.json", api_clients_map_doc, max_bytes=cfg.max_shard_bytes)

    # ---------------------------------------------------------------------
    # v6/v7 analyzers (Symbols per language)
    # ---------------------------------------------------------------------
    
    symbol_analyzers = [
        ("python", python_symbols.analyze, py_files, "python.symbols.json"),
        ("typescript", typescript_symbols.analyze, scan.get("ts_files", []), "typescript.symbols.json"),
        ("vue", vue_analyzer.analyze, scan.get("vue_files", []), "vue.symbols.json"),
        ("go", go_symbols.analyze, go_files, "go.symbols.json"),
        ("rust", rust_symbols.analyze, rs_files, "rust.symbols.json"),
    ]

    for lang_name, analyzer_func, files, out_fn in symbol_analyzers:
        doc = safe_analyze(f"{lang_name}_symbols", analyzer_func, cfg, files, pid_by_path)
        if doc:
            if doc.get("errors"):
                errors.extend([{"analyzer": f"{lang_name}_symbols", "error": str(e)} for e in doc["errors"]])
            filenames[out_fn] = write.write_json_sharded(
                final_out / out_fn, doc, "symbols", max_bytes=cfg.max_shard_bytes
            )

    # ---------------------------------------------------------------------
    # v3 analyzers (Gradle/Kotlin/Kafka Streams)
    # ---------------------------------------------------------------------

    # Gradle module/deps discovery
    gradle_doc = safe_analyze("gradle_modules", gradle_modules.analyze, cfg, gradle_files, pid_by_path)
    if gradle_doc:
        write.write_json(final_out / "gradle.modules.json", gradle_doc, max_bytes=cfg.max_shard_bytes)

    # Kotlin symbols
    kotlin_symbols_doc = safe_analyze("kotlin_symbols", kotlin_symbols.analyze, cfg, kt_files_for_symbols, pid_by_path)
    if kotlin_symbols_doc:
        if kotlin_symbols_doc.get("errors"):
            errors.extend([{"analyzer": "kotlin_symbols", "error": str(e)} for e in kotlin_symbols_doc["errors"]])
        filenames["kotlin.symbols.json"] = write.write_json_sharded(
            final_out / "kotlin.symbols.json", kotlin_symbols_doc, "symbols", max_bytes=cfg.max_shard_bytes
        )
    else:
        kotlin_symbols_doc = {"symbols": []}

    # -------------------------------------
    # v5 analyzers (Graph Relationship Shards)
    # -------------------------------------
    try:
        # 1. Global Symbol Registry
        registry = symbol_registry.analyze(cfg, kt_files_for_symbols, pid_by_path)
        
        # 2. Call Graph
        call_graph = kotlin_calls.analyze(cfg, kt_files_for_symbols, pid_by_path, registry)
        
        # 3. Inheritance
        inheritance_graph = inheritance.analyze(cfg, kt_files_for_symbols, pid_by_path, registry)

        # Kafka Streams EDA
        kafka_eda_doc = kafka_streams_eda.analyze(
            cfg, kt_files_for_eda, config_files, pid_by_path, registry=registry
        )
        if kafka_eda_doc:
            write.write_json(final_out / "kafka.streams_eda.json", kafka_eda_doc, max_bytes=cfg.max_shard_bytes)

        # Serialization
        calls_shard = graph_serialization.serialize_graph_to_edge_list(call_graph, shard_id="kotlin_calls")
        write.write_json(final_out / "kotlin_calls.json", calls_shard, max_bytes=cfg.max_shard_bytes)

        inheritance_shard = graph_serialization.serialize_graph_to_edge_list(inheritance_graph, shard_id="inheritance")
        write.write_json(final_out / "inheritance.json", inheritance_shard, max_bytes=cfg.max_shard_bytes)

        inverse_call_graph = call_graph.reverse(copy=True)
        inverse_shard = graph_serialization.serialize_graph_to_edge_list(inverse_call_graph, shard_id="inverse_calls")
        write.write_json(final_out / "inverse_calls.json", inverse_shard, max_bytes=cfg.max_shard_bytes)

        # GSI (Global Symbol Index)
        gsi = {qn: "kotlin_calls.json" for qn in registry.symbols.keys()}
        
        metadata = {
            "version": "1.0.0",
            "status": "partial" if errors else "success",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "duration_sec": (datetime.now(timezone.utc) - start_time).total_seconds(),
            "capabilities": ["call_graph", "inheritance", "kafka_bridge", "go_support", "rust_support", "sharding"],
            "errors": errors,
            "gsi": gsi
        }
        write.write_json(final_out / "metadata.json", metadata, max_bytes=cfg.max_shard_bytes)
    except Exception as exc:
        print(f"[digest_tool_v3] ERROR: Failed to analyze Graph/Kafka relations: {exc}")
        errors.append({"analyzer": "graph_v5", "error": str(exc)})
        metadata = {
            "version": "1.0.0",
            "status": "partial",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "duration_sec": (datetime.now(timezone.utc) - start_time).total_seconds(),
            "errors": errors,
        }
        write.write_json(final_out / "metadata.json", metadata, max_bytes=cfg.max_shard_bytes)

    # nav.json
    _write_nav(
        final_out,
        run_id,
        filenames,
        include_ctor=include_ctor_items,
        include_fi=include_files_index,
        max_bytes=cfg.max_shard_bytes,
    )

    # Optional bundle
    if cfg.bundle_all:
        make_bundle(final_out, gzip_out=cfg.bundle_gzip)
