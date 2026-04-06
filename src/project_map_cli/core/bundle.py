# utils/digest_tool_v3/bundle.py
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _maybe_read(out_dir: Path, rel: str | None) -> tuple[str, Any] | None:
    """
    If rel is a non-empty string and the file exists under out_dir, load it.
    Returns (rel, doc) or None.
    """
    if not rel:
        return None
    p = out_dir / rel
    if not p.exists() or not p.is_file():
        return None
    try:
        return (rel, _read_json(p))
    except Exception:
        return None


def _collect_dir_jsons(dir_path: Path, suffix: str = ".json") -> Dict[str, Any]:
    """
    Collect all JSON files under dir_path (non-recursive) into a dict keyed by filename.
    Deterministic order by filename.
    """
    collected: Dict[str, Any] = {}
    if not dir_path.exists() or not dir_path.is_dir():
        return collected
    for p in sorted(dir_path.glob(f"*{suffix}"), key=lambda q: q.name):
        try:
            collected[p.name] = _read_json(p)
        except Exception:
            continue
    return collected


def _route_key(path: str, method: str) -> str:
    return f"{path}#{(method or '').upper()}"


def _nav_get(nav: Any, key: str, default: Any = None) -> Any:
    """Safe nav.get that never throws if nav is malformed."""
    if isinstance(nav, dict):
        return nav.get(key, default)
    return default


def _get_doc(bundle: Dict[str, Any], nav: Any, nav_key: str, default_rel: str) -> Dict[str, Any]:
    """
    Fetch a shard doc from the bundle using nav mapping.
    Returns {} unless the shard exists AND is a dict.
    """
    rel = _nav_get(nav, nav_key, None) or default_rel
    doc = bundle.get(rel, {})
    return doc if isinstance(doc, dict) else {}


def _has_dict_shard(bundle: Dict[str, Any], nav: Any, nav_key: str, default_rel: str) -> bool:
    """Shard is considered present only if loaded into bundle AND top-level JSON is a dict."""
    rel = _nav_get(nav, nav_key, None) or default_rel
    return isinstance(rel, str) and isinstance(bundle.get(rel, None), dict)


def _cap_list(xs: List[Any], n: int) -> List[Any]:
    if n <= 0:
        return []
    if len(xs) <= n:
        return xs
    return xs[:n]


def _safe_int(x: Any, default: int = -1) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _safe_str(x: Any) -> str:
    try:
        return str(x or "")
    except Exception:
        return ""


# ------------------------
# LLM header construction
# ------------------------

def _build_indices_fast(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build tiny indices from already-loaded shards inside the bundle.

    Determinism:
      - json dump uses sort_keys=True (see make_all)
      - list-like indices explicitly sorted where order could vary
    """
    nav = bundle.get("nav", {}) or {}
    if not isinstance(nav, dict):
        nav = {}

    # paths.json is the foundation
    paths_rel = _nav_get(nav, "paths_map", None) or "paths.json"
    paths = bundle.get(paths_rel, {}) or {}
    if not isinstance(paths, dict):
        paths = {}

    path_by_pid: Dict[str, str] = {str(pid): str(path) for pid, path in paths.items()}
    pid_by_path: Dict[str, int] = {v: int(k) for k, v in path_by_pid.items() if str(k).isdigit()}

    # routes_by_key
    api_routes = _get_doc(bundle, nav, "api_routes", "api.routes.json")
    routes_by_key: Dict[str, Dict[str, Any]] = {}
    for r in api_routes.get("routes", []) or []:
        if not isinstance(r, dict):
            continue
        try:
            key = _route_key(_safe_str(r.get("path", "")), _safe_str(r.get("method", "")))
            handler = r.get("handler", {}) or {}
            if not isinstance(handler, dict):
                continue
            routes_by_key[key] = {
                "pid": _safe_int(handler.get("pid", -1)),
                "qualname": _safe_str(handler.get("qualname", "")),
            }
        except Exception:
            continue

    # models_by_name
    pyd = _get_doc(bundle, nav, "pydantic_models", "pydantic.models.json")
    models_by_name: Dict[str, Dict[str, Any]] = {}
    for m in pyd.get("models", []) or []:
        if not isinstance(m, dict):
            continue
        name = _safe_str(m.get("name", ""))
        if not name:
            continue
        models_by_name[name] = {
            "module": _safe_str(m.get("module", "")),
            "hash": _safe_str(m.get("hash", "")),
        }

    # tables_by_name
    dbs = _get_doc(bundle, nav, "db_schema", "db.schema.json")
    tables_by_name = {
        _safe_str(t.get("name", "")): True
        for t in (dbs.get("tables", []) or [])
        if isinstance(t, dict) and t.get("name")
    }

    indices: Dict[str, Any] = {
        "pid_by_path": pid_by_path,
        "path_by_pid": path_by_pid,
        "routes_by_key": routes_by_key,
        "models_by_name": models_by_name,
        "tables_by_name": tables_by_name,
    }

    # ---- v3: Gradle/Kotlin/Kafka Streams indices ONLY if shards exist (and are dicts) ----

    # gradle.modules
    if _has_dict_shard(bundle, nav, "gradle_modules", "gradle.modules.json"):
        gradle_doc = _get_doc(bundle, nav, "gradle_modules", "gradle.modules.json")
        gradle_modules_idx: Dict[str, Any] = {}
        for m in gradle_doc.get("modules", []) or []:
            if not isinstance(m, dict):
                continue
            name = _safe_str(m.get("name", ""))
            if not name:
                continue
            gradle_modules_idx[name] = {
                "path": _safe_str(m.get("path", "")),
                "kafka": bool(m.get("kafka", False)),
                "kotlinx_serialization": bool(m.get("kotlinx_serialization", False)),
            }
        indices["gradle"] = {"modules": gradle_modules_idx}

    # kotlin.symbols_by_name
    if _has_dict_shard(bundle, nav, "kotlin_symbols", "kotlin.symbols.json"):
        kotlin_doc = _get_doc(bundle, nav, "kotlin_symbols", "kotlin.symbols.json")
        symbols_by_name: Dict[str, List[Dict[str, Any]]] = {}
        for sym in kotlin_doc.get("symbols", []) or []:
            if not isinstance(sym, dict):
                continue
            nm = _safe_str(sym.get("name", ""))
            if not nm:
                continue
            rec = {
                "pid": _safe_int(sym.get("pid", -1)),
                "ln": _safe_int(sym.get("ln", -1)),
                "kind": _safe_str(sym.get("kind", "")),
                "qname": _safe_str(sym.get("qname", "")),
            }
            symbols_by_name.setdefault(nm, []).append(rec)

        for nm in list(symbols_by_name.keys()):
            symbols_by_name[nm] = sorted(
                symbols_by_name[nm],
                key=lambda r: (r.get("pid", -1), r.get("ln", -1), r.get("kind", ""), r.get("qname", "")),
            )
        indices["kotlin"] = {"symbols_by_name": symbols_by_name}

    # v6: Python symbols
    if _has_dict_shard(bundle, nav, "python_symbols", "python.symbols.json"):
        py_doc = _get_doc(bundle, nav, "python_symbols", "python.symbols.json")
        py_symbols: Dict[str, List[Dict[str, Any]]] = {}
        for sym in py_doc.get("symbols", []) or []:
            if not isinstance(sym, dict): continue
            nm = _safe_str(sym.get("name", ""))
            if not nm: continue
            rec = {
                "pid": _safe_int(sym.get("pid", -1)),
                "ln": _safe_int(sym.get("ln", -1)),
                "kind": _safe_str(sym.get("kind", "")),
                "qname": _safe_str(sym.get("qname", "")),
                "decorators": list(sym.get("decorators", []) or [])
            }
            py_symbols.setdefault(nm, []).append(rec)
        indices["python_symbols"] = py_symbols

    # v6: TypeScript symbols
    if _has_dict_shard(bundle, nav, "typescript_symbols", "typescript.symbols.json"):
        ts_doc = _get_doc(bundle, nav, "typescript_symbols", "typescript.symbols.json")
        ts_symbols: Dict[str, List[Dict[str, Any]]] = {}
        for sym in ts_doc.get("symbols", []) or []:
            if not isinstance(sym, dict): continue
            nm = _safe_str(sym.get("name", ""))
            if not nm: continue
            rec = {
                "pid": _safe_int(sym.get("pid", -1)),
                "ln": _safe_int(sym.get("ln", -1)),
                "kind": _safe_str(sym.get("kind", "")),
                "qname": _safe_str(sym.get("qname", "")),
                "isTypeOnly": bool(sym.get("isTypeOnly", False)),
                "external_patterns": list(sym.get("external_patterns", []) or [])
            }
            ts_symbols.setdefault(nm, []).append(rec)
        indices["typescript_symbols"] = ts_symbols

    # v6: Vue components
    if _has_dict_shard(bundle, nav, "vue_symbols", "vue.symbols.json"):
        vue_doc = _get_doc(bundle, nav, "vue_symbols", "vue.symbols.json")
        vue_idx: Dict[str, Dict[str, Any]] = {}
        for comp in vue_doc.get("components", []) or []:
            if not isinstance(comp, dict): continue
            nm = _safe_str(comp.get("name", ""))
            if not nm: continue
            vue_idx[nm] = {
                "pid": _safe_int(comp.get("pid", -1)),
                "qname": _safe_str(comp.get("qname", "")),
                "used_components": list(comp.get("used_components", []) or [])
            }
        indices["vue"] = {"components": vue_idx}

    # kafka.* indices
    if _has_dict_shard(bundle, nav, "kafka_streams_eda", "kafka.streams_eda.json"):
        kafka_doc = _get_doc(bundle, nav, "kafka_streams_eda", "kafka.streams_eda.json")

        # topics: topic_key -> occurrences[]
        topics_idx: Dict[str, List[Dict[str, Any]]] = {}
        for t in kafka_doc.get("topics", []) or []:
            if not isinstance(t, dict):
                continue
            topic_key = _safe_str(t.get("topic", "") or t.get("topic_ref", "") or "")
            if not topic_key:
                continue
            occ = {
                "topic": _safe_str(t.get("topic", "")),
                "topic_ref": _safe_str(t.get("topic_ref", "")),
                "op": _safe_str(t.get("op", "")),
                "pid": _safe_int(t.get("pid", -1)),
                "ln": _safe_int(t.get("ln", -1)),
                "file": _safe_str(t.get("file", "")),
            }
            topics_idx.setdefault(topic_key, []).append(occ)

        # deterministic ordering + small cap per topic to avoid blow-ups
        for k in list(topics_idx.keys()):
            topics_idx[k] = _cap_list(
                sorted(
                    topics_idx[k],
                    key=lambda r: (r["op"], r["pid"], r["ln"], r["topic_ref"], r["topic"], r["file"]),
                ),
                20,
            )

        # edges: support both legacy DSL edges and new Processor forward_route edges
        edges_idx: List[Dict[str, Any]] = []
        for e in kafka_doc.get("edges", []) or []:
            if not isinstance(e, dict):
                continue

            edge_kind = _safe_str(e.get("edge_kind", ""))  # NEW (forward_route)
            if not edge_kind:
                # Back-compat: treat missing edge_kind as DSL
                edge_kind = "dsl"

            edges_idx.append(
                {
                    "edge_kind": edge_kind,
                    "from": _safe_str(e.get("from", "")),
                    "from_ref": _safe_str(e.get("from_ref", "")),
                    "to": _safe_str(e.get("to", "")),
                    "to_ref": _safe_str(e.get("to_ref", "")),
                    "via": list(e.get("via", []) or []),
                    "via_kind": _safe_str(e.get("via_kind", "")),  # optional (e.g. "Processor.forward")
                    "pid": _safe_int(e.get("pid", -1)),
                    "ln": _safe_int(e.get("ln", -1)),
                    "file": _safe_str(e.get("file", "")),
                }
            )

        edges_idx = sorted(
            edges_idx,
            key=lambda r: (
                r["edge_kind"],
                r.get("from", ""),
                r.get("from_ref", ""),
                r.get("to", ""),
                r.get("to_ref", ""),
                r.get("pid", -1),
                r.get("ln", -1),
                r.get("file", ""),
            ),
        )

        # routes: Route.X -> occurrences + inbound forward_route edges
        routes_idx: Dict[str, Dict[str, Any]] = {}

        def _ensure_route(route_ref: str) -> Dict[str, Any]:
            if route_ref not in routes_idx:
                routes_idx[route_ref] = {"occurrences": [], "in_edges": []}
            return routes_idx[route_ref]

        # occurrences are taken from edges where to_ref looks like Route.X OR to like Route.X
        for ed in edges_idx:
            ek = _safe_str(ed.get("edge_kind", ""))
            to_ref = _safe_str(ed.get("to_ref", ""))
            to = _safe_str(ed.get("to", ""))
            route = ""

            # prefer explicit to_ref
            if to_ref.startswith("Route."):
                route = to_ref
            elif to.startswith("Route."):
                route = to

            if not route:
                continue

            rec = {
                "edge_kind": ek,
                "pid": ed.get("pid", -1),
                "ln": ed.get("ln", -1),
                "file": ed.get("file", ""),
                "from": ed.get("from", ""),
                "from_ref": ed.get("from_ref", ""),
                "to": ed.get("to", ""),
                "to_ref": ed.get("to_ref", ""),
                "via_kind": ed.get("via_kind", ""),
            }

            bucket = _ensure_route(route)
            bucket["occurrences"].append(rec)
            if ek == "forward_route":
                bucket["in_edges"].append(rec)

        # deterministic order + cap to avoid blow ups
        for rkey in list(routes_idx.keys()):
            occs = routes_idx[rkey]["occurrences"]
            occs = sorted(occs, key=lambda x: (x["edge_kind"], x["pid"], x["ln"], x["file"]))
            routes_idx[rkey]["occurrences"] = _cap_list(occs, 50)

            ins = routes_idx[rkey]["in_edges"]
            ins = sorted(ins, key=lambda x: (x["pid"], x["ln"], x["file"]))
            routes_idx[rkey]["in_edges"] = _cap_list(ins, 50)

        # serde refs (support either key name)
        serde_rows = kafka_doc.get("serde_refs", []) or kafka_doc.get("serdes", []) or []
        serde_refs_idx: List[Dict[str, Any]] = []
        for s in serde_rows:
            if not isinstance(s, dict):
                continue
            serde_refs_idx.append(
                {
                    "kind": _safe_str(s.get("kind", "")),
                    "ref": _safe_str(s.get("ref", "") or s.get("name", "")),
                    "topic": _safe_str(s.get("topic", "") or s.get("topic_ref", "")),
                    "pid": _safe_int(s.get("pid", -1)),
                    "ln": _safe_int(s.get("ln", -1)),
                    "file": _safe_str(s.get("file", "")),
                }
            )
        serde_refs_idx = sorted(
            serde_refs_idx, key=lambda r: (r["kind"], r["ref"], r["topic"], r["pid"], r["ln"], r["file"])
        )

        indices["kafka"] = {
            "topics": topics_idx,
            "edges": edges_idx,
            "routes": routes_idx,  # NEW
            "serde_refs": serde_refs_idx,
        }

    return indices


def _build_llm_map_min(bundle: Dict[str, Any], max_edges: int = 10000) -> Dict[str, Any]:
    """
    Minimal FE→Route→Handler→Model edges (always).
    Optional Kafka breadcrumb hop ONLY if kafka.streams_eda.json exists AND is a dict.
    """
    nav = bundle.get("nav", {}) or {}
    if not isinstance(nav, dict):
        nav = {}

    paths_rel = _nav_get(nav, "paths_map", None) or "paths.json"
    paths = bundle.get(paths_rel, {}) or {}
    if not isinstance(paths, dict):
        paths = {}

    api_map = _get_doc(bundle, nav, "api_clients_map", "api_clients.map.json")
    api_routes = _get_doc(bundle, nav, "api_routes", "api.routes.json")
    pyd = _get_doc(bundle, nav, "pydantic_models", "pydantic.models.json")

    kafka_present = _has_dict_shard(bundle, nav, "kafka_streams_eda", "kafka.streams_eda.json")
    kafka_doc = _get_doc(bundle, nav, "kafka_streams_eda", "kafka.streams_eda.json") if kafka_present else {}

    route_index: Dict[str, Dict[str, Any]] = {}
    for r in api_routes.get("routes", []) or []:
        if not isinstance(r, dict):
            continue
        key = _route_key(_safe_str(r.get("path", "")), _safe_str(r.get("method", "")))
        route_index[key] = r

    model_modules = {
        m.get("name"): m.get("module")
        for m in (pyd.get("models", []) or [])
        if isinstance(m, dict) and m.get("name")
    }

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    def add_node(key: str, val: Dict[str, Any]) -> None:
        if key not in nodes:
            nodes[key] = val

    def add_edge(src: str, dst: str, rel: str) -> None:
        if len(edges) < max_edges:
            edges.append({"src": src, "dst": dst, "rel": rel})

    # FE → API → Model
    for link in sorted(api_map.get("links", []) or [], key=lambda x: _safe_str(x.get("route", ""))):
        if not isinstance(link, dict):
            continue
        rk = _safe_str(link.get("route", ""))
        if not rk:
            continue

        add_node(
            f"route:{rk}",
            {"kind": "route", "path": rk.split("#")[0], "method": rk.split("#")[1] if "#" in rk else ""},
        )

        clients = [c for c in (link.get("clients", []) or []) if isinstance(c, dict)]
        for cl in sorted(clients, key=lambda c: (_safe_str(c.get("component", "")), _safe_int(c.get("pid", -1)))):
            pid = _safe_int(cl.get("pid", -1))
            if pid >= 0:
                add_node(f"pid:{pid}", {"kind": "file", "path": _safe_str(paths.get(str(pid), ""))})
                add_edge(f"pid:{pid}", f"route:{rk}", "fe_calls")

        r = route_index.get(rk)
        if r:
            handler = r.get("handler", {}) or {}
            if not isinstance(handler, dict):
                continue
            hpid = _safe_int(handler.get("pid", -1))
            qual = _safe_str(handler.get("qualname", ""))
            if qual:
                add_node(
                    f"mod:{qual}",
                    {"kind": "py-func", "pid": hpid, "module": qual.rsplit(".", 1)[0], "qualname": qual},
                )
                add_edge(f"route:{rk}", f"mod:{qual}", "handled_by")

                rm = r.get("response_model")
                if rm:
                    mod = model_modules.get(rm)
                    add_node(f"pyd:{rm}", {"kind": "pyd-model", "module": _safe_str(mod or "")})
                    add_edge(f"mod:{qual}", f"pyd:{rm}", "returns_model")

    hops = [{"name": "fe→api→model", "path": ["fe_calls", "handled_by", "returns_model"]}]

    # Optional Kafka: topic/metadata → forward_route → Route.X → (future router) → topic
    if kafka_present:
        # Create nodes for topics and routes
        for t in kafka_doc.get("topics", []) or []:
            if not isinstance(t, dict):
                continue
            topic_key = _safe_str(t.get("topic", "") or t.get("topic_ref", "") or "")
            if topic_key:
                add_node(f"topic:{topic_key}", {"kind": "kafka-topic", "name": topic_key})

        # Special token used by analyzer: "<recordMetadata.topic>"
        add_node("topic:<recordMetadata.topic>", {"kind": "kafka-topic", "name": "<recordMetadata.topic>", "dynamic": True})

        # Route nodes are implicit (derived from edges)
        for e in kafka_doc.get("edges", []) or []:
            if not isinstance(e, dict):
                continue

            edge_kind = _safe_str(e.get("edge_kind", "")) or "dsl"
            fr = _safe_str(e.get("from", "") or e.get("from_ref", "") or "")
            to_ref = _safe_str(e.get("to_ref", "") or "")
            to = _safe_str(e.get("to", "") or "")

            pid = _safe_int(e.get("pid", -1))
            ln = _safe_int(e.get("ln", -1))

            # Node key for this topology edge
            edge_key = f"kedge:{edge_kind}:{fr}→{to_ref or to}@{pid}:{ln}"
            add_node(edge_key, {"kind": "kafka-edge", "edge_kind": edge_kind, "from": fr, "to": to, "to_ref": to_ref, "pid": pid, "ln": ln})

            # Source node: topic or metadata token
            src_node = f"topic:{fr}" if fr else "topic:<recordMetadata.topic>"
            if src_node not in nodes:
                # Create if missing
                add_node(src_node, {"kind": "kafka-topic", "name": fr or "<recordMetadata.topic>"})

            add_edge(src_node, edge_key, "topology_edge")

            # Route hop if forward_route
            if edge_kind == "forward_route":
                route = to_ref if to_ref.startswith("Route.") else (to if to.startswith("Route.") else "")
                if route:
                    add_node(f"route_ref:{route}", {"kind": "kafka-route-ref", "name": route})
                    add_edge(edge_key, f"route_ref:{route}", "forward_route")

            # Defined-in link
            if pid >= 0:
                add_node(f"pid:{pid}", {"kind": "file", "path": _safe_str(paths.get(str(pid), ""))})
                add_edge(edge_key, f"pid:{pid}", "defined_in")

        hops.append({"name": "topic→forward_route→route_ref", "path": ["topology_edge", "forward_route"]})
        hops.append({"name": "topic→topology→file", "path": ["topology_edge", "defined_in"]})

    edges = sorted(edges, key=lambda e: (_safe_str(e.get("src", "")), _safe_str(e.get("dst", "")), _safe_str(e.get("rel", ""))))

    return {
        "version": "3.0",
        "nodes_count": len(nodes),
        "edges_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "hops": hops,
    }


def _build_playbooks(kafka_present: bool, nav_has_kafka_key: bool) -> Dict[str, Any]:
    """
    Playbooks live in the bundle (not in your Kafka repo).
    Requirement: include Kafka playbooks ONLY if:
      - nav contains kafka shard keys AND
      - bundle successfully loaded kafka.streams_eda.json (kafka_present)
    """
    playbooks: List[Dict[str, Any]] = [
        {
            "id": "db-shape-drift",
            "load": ["__01_indices_fast", "db.schema.json", "pydantic.models.json"],
            "steps": [
                "Map model→table by name/module hints.",
                "Check nullability/type mismatches; propose migration.",
            ],
            "accept": ["table + model names", "field-level mismatches + migration sketch"],
        },
        {
            "id": "fe-api-mismatch",
            "load": ["__01_indices_fast", "api_clients.map.json", "api.routes.json", "pydantic.models.json"],
            "steps": [
                "Normalize FE route as '/path#METHOD'. Lookup routes_by_key.",
                "Open handler by pid (paths.json). Compare response_model vs FE usage.",
                "Return exact PIDs, paths, and diff of fields.",
            ],
            "accept": ["handler.qualname, pid, path", "model-field vs FE param mismatch list", "concrete patch locations"],
        },
    ]

    if kafka_present and nav_has_kafka_key:
        playbooks.extend(
            [
                {
                    "id": "kafka-serde-sanity",
                    "load": ["__01_indices_fast", "kafka.streams_eda.json", "kotlin.symbols.json", "paths.json"],
                    "steps": [
                        "For each topic, list serde refs (__01_indices_fast.kafka.serde_refs) and where they apply (Consumed/Produced).",
                        "Report missing/odd serde patterns and where to patch (pid/path/ln).",
                    ],
                    "accept": ["topic -> serde list with pid/path/ln", "warnings list", "concrete patch locations"],
                },
                {
                    "id": "kafka-topology-entrypoints",
                    "load": ["kotlin.symbols.json", "gradle.modules.json", "paths.json"],
                    "steps": [
                        "Rank likely topology builders/factories (symbol names containing Streams/Topology/Factory/build/create/Main).",
                        "Group by module and list files/pids/lns.",
                    ],
                    "accept": ["module -> entrypoint symbols list", "pid/path/ln for each"],
                },
                {
                    "id": "kafka-trace-topic",
                    "load": ["__01_indices_fast", "kafka.streams_eda.json", "paths.json"],
                    "steps": [
                        "Normalize topic key using __01_indices_fast.kafka.topics keys (prefer resolved topic; else topic_ref).",
                        "List occurrences and edges involving the topic.",
                        "Open the defining file by pid and locate topology code near ln.",
                    ],
                    "accept": ["topic occurrences", "upstream/downstream edges", "topology file locations (pid/path/ln)"],
                },
                {
                    "id": "kafka-forward-route",
                    "load": ["__01_indices_fast", "kafka.streams_eda.json", "kotlin.symbols.json", "paths.json"],
                    "steps": [
                        "Search __01_indices_fast.kafka.routes for Route.* keys.",
                        "For each, list inbound forward_route edges (pid/path/ln) and open those files.",
                        "Use kotlin.symbols.json to locate enum declaration and entries; verify route set is bounded.",
                    ],
                    "accept": ["Route.* -> inbound edges list", "enum declaration location", "concrete file anchors"],
                },
            ]
        )

    playbooks = sorted(playbooks, key=lambda p: _safe_str(p.get("id", "")))
    return {"version": "3.0", "playbooks": playbooks}


def _build_contract(
    bundle: Dict[str, Any],
    nav: Dict[str, Any],
    gradle_present: bool,
    kotlin_present: bool,
    kafka_present: bool
) -> Dict[str, Any]:
    available_indices: Dict[str, List[str]] = {
        "python": ["routes_by_key", "models_by_name", "tables_by_name", "pid_by_path", "path_by_pid"]
    }
    if gradle_present:
        available_indices["gradle"] = ["gradle.modules"]
    if kotlin_present:
        available_indices["kotlin"] = ["kotlin.symbols_by_name"]
    
    # v6 additions
    if _has_dict_shard(bundle, nav, "python_symbols", "python.symbols.json"):
        available_indices["python_symbols"] = ["python_symbols"]
    if _has_dict_shard(bundle, nav, "typescript_symbols", "typescript.symbols.json"):
        available_indices["typescript_symbols"] = ["typescript_symbols"]
    if _has_dict_shard(bundle, nav, "vue_symbols", "vue.symbols.json"):
        available_indices["vue"] = ["vue.components"]

    if kafka_present:
        available_indices["kafka"] = ["kafka.topics", "kafka.edges", "kafka.routes", "kafka.serde_refs"]

    process: List[str] = [
        "Load __01_indices_fast and nav.json.",
        "If question mentions a route, normalize to '/path#METHOD' and use routes_by_key.",
        "Follow __03_llm_map_min.hops for FE→Route→Handler→Model.",
    ]
    if kafka_present:
        process.append("If question mentions a Kafka topic, start with __01_indices_fast.kafka.topics and kafka.edges.")
        process.append("If question mentions Route.*, start with __01_indices_fast.kafka.routes.")
        process.append("Follow __03_llm_map_min.hops for topic→forward_route→route_ref and topic→topology→file.")
    process.extend(
        [
            "Open only files by PID via paths.json; avoid scanning unrelated files.",
            "Pull a single files_index shard when structure is needed.",
            "Return PIDs, paths, and shards you read.",
        ]
    )

    return {
        "version": "3.0",
        "policy": {"deny_full_scan": True, "max_files_to_open": 15, "must_cite": ["pid", "path", "shards_used"]},
        "available_indices": available_indices,
        "process": process,
    }


# ------------------------
# Public API
# ------------------------

def make_all(out_dir: Path, gzip_out: bool = False) -> Path:
    """
    Build a single-file bundle (all.json or all.json.gz).

    Determinism:
      - Directory collections keyed by filename (sorted)
      - json dump uses sort_keys=True + minimal separators
      - Lists that could vary are explicitly sorted in builders
    """
    out_dir = Path(out_dir)
    nav_path = out_dir / "nav.json"
    if not nav_path.exists():
        raise FileNotFoundError(f"nav.json not found in {out_dir}")

    nav = _read_json(nav_path)
    if not isinstance(nav, dict):
        # fail closed: refuse to bundle if nav isn't a dict (it should be)
        raise ValueError("nav.json is not an object/dict; cannot bundle deterministically")

    bundle: Dict[str, Any] = {"nav": nav}

    # Top-level shards referenced by nav (load if they exist)
    top_keys: List[Tuple[str, str]] = [
        ("digest", "digest.top.json"),
        ("analyzers", "analyzers.repo_only.json"),
        ("ctor_top", "ctor.top.json"),
        ("paths_map", "paths.json"),
        ("api_routes", "api.routes.json"),
        ("pydantic_models", "pydantic.models.json"),
        ("fe_calls", "fe.calls.json"),
        ("db_schema", "db.schema.json"),
        ("api_clients_map", "api_clients.map.json"),
        # v3 additions
        ("gradle_modules", "gradle.modules.json"),
        ("kotlin_symbols", "kotlin.symbols.json"),
        ("kafka_streams_eda", "kafka.streams_eda.json"),
        # v6 additions
        ("python_symbols", "python.symbols.json"),
        ("typescript_symbols", "typescript.symbols.json"),
        ("vue_symbols", "vue.symbols.json"),
    ]

    for nav_key, default_rel in top_keys:
        rel = nav.get(nav_key) or default_rel
        pair = _maybe_read(out_dir, rel)
        if pair is not None:
            k, doc = pair
            bundle[k] = doc

    # Optional dirs
    ctor_root = nav.get("ctor_items_root")
    if isinstance(ctor_root, str) and ctor_root:
        bundle["ctor.items"] = _collect_dir_jsons(out_dir / ctor_root)

    fi_root = nav.get("files_index_root")
    if isinstance(fi_root, str) and fi_root:
        bundle["files_index"] = _collect_dir_jsons(out_dir / fi_root, suffix=".min.json")

    # Presence flags: shard must exist AND be a dict
    gradle_present = _has_dict_shard(bundle, nav, "gradle_modules", "gradle.modules.json")
    kotlin_present = _has_dict_shard(bundle, nav, "kotlin_symbols", "kotlin.symbols.json")
    kafka_present = _has_dict_shard(bundle, nav, "kafka_streams_eda", "kafka.streams_eda.json")

    # nav key presence (explicit requirement)
    nav_has_kafka_key = isinstance(nav.get("kafka_streams_eda", None), str) and bool(nav.get("kafka_streams_eda"))

    # LLM header sections
    indices = _build_indices_fast(bundle)
    llm_map_min = _build_llm_map_min(bundle, max_edges=10000)
    playbooks = _build_playbooks(kafka_present=kafka_present, nav_has_kafka_key=nav_has_kafka_key)
    contract = _build_contract(
        bundle=bundle,
        nav=nav,
        gradle_present=gradle_present,
        kotlin_present=kotlin_present,
        kafka_present=kafka_present
    )

    bundle["__00_llm_contract"] = contract
    bundle["__01_indices_fast"] = indices
    bundle["__02_playbooks"] = playbooks
    bundle["__03_llm_map_min"] = llm_map_min

    out_path = out_dir / ("all.json.gz" if gzip_out else "all.json")
    if gzip_out:
        with gzip.open(out_path, "wt", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    else:
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    return out_path
