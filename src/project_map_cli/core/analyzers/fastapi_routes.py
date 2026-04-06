# utils/digest_tool_v3/analyzers/fastapi_routes.py
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..common import ast_utils


_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


@dataclass(frozen=True)
class _DecoratedRoute:
    owner: str              # variable name of app/router (e.g., "app", "router_v1")
    method: str             # "GET", "POST", ...
    path: str               # literal path from decorator ("/api/x")
    ln: int                 # function line number (for stability)
    response_model: Optional[str]
    tags: List[str]
    dependencies: List[str]
    func_name: str          # python function name


def _is_fastapi_ctor(node: ast.AST) -> bool:
    """Return True if node is a call to FastAPI()."""
    if not isinstance(node, ast.Call):
        return False
    name = _attr_to_str(node.func)
    return name.endswith("FastAPI")


def _is_router_ctor(node: ast.AST) -> bool:
    """Return True if node is a call to APIRouter()."""
    if not isinstance(node, ast.Call):
        return False
    name = _attr_to_str(node.func)
    return name.endswith("APIRouter")


def _attr_to_str(node: ast.AST) -> str:
    """dotted string for Name/Attribute."""
    s = ast_utils._attr_to_str(node)
    return s or ""


def _const_str(node: ast.AST) -> Optional[str]:
    """Return str value if node is a literal string; else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _extract_tags(val: ast.AST) -> List[str]:
    """Extract ['a','b'] from tags=[...] where elements are literal strings."""
    if isinstance(val, (ast.List, ast.Tuple)):
        out: List[str] = []
        for el in val.elts:
            s = _const_str(el)
            if s is not None:
                out.append(s)
        return out
    # Non-literals: we won’t try to resolve; keep empty for determinism/size.
    return []


def _extract_dependencies(val: ast.AST) -> List[str]:
    """
    Extract a compact representation of dependencies.
    Prefer stringifying Depends target: Depends(pkg.auth.fn) -> 'Depends:pkg.auth.fn'
    """
    deps: List[str] = []
    if isinstance(val, (ast.List, ast.Tuple)):
        for el in val.elts:
            if isinstance(el, ast.Call) and _attr_to_str(el.func).endswith("Depends"):
                target = ""
                if el.args:
                    target = _attr_to_str(el.args[0]) or _const_str(el.args[0]) or ""
                if target:
                    deps.append(f"Depends:{target}")
                else:
                    deps.append("Depends")
    return deps


def _extract_response_model(val: ast.AST) -> Optional[str]:
    """Return dotted name for response_model if it’s a simple Name/Attribute."""
    s = _attr_to_str(val)
    return s or None


def _first_param_model(func: ast.FunctionDef) -> Optional[str]:
    """
    Best-effort: return the first parameter annotation that looks like a model
    (Name or dotted Attribute). We do not resolve base classes here.
    """
    for arg in list(func.args.args) + list(func.args.kwonlyargs):
        ann = getattr(arg, "annotation", None)
        if isinstance(ann, (ast.Name, ast.Attribute)):
            s = _attr_to_str(ann)
            if s:
                return s
    return None


def _collect_apps_and_routers(tree: ast.AST) -> Tuple[List[str], List[str], List[Tuple[str, str, str]]]:
    """
    Return (apps, routers, include_edges)
    - apps: variable names bound to FastAPI()
    - routers: variable names bound to APIRouter()
    - include_edges: (parent_name, child_router_name, prefix_str)
    """
    apps: List[str] = []
    routers: List[str] = []
    edges: List[Tuple[str, str, str]] = []

    class V(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            if isinstance(node.value, ast.Call):
                if _is_fastapi_ctor(node.value):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            apps.append(t.id)
                elif _is_router_ctor(node.value):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            routers.append(t.id)
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            # parent.include_router(child, prefix="/x")
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "include_router":
                parent = _attr_to_str(fn.value)  # Name or dotted attr; we only handle simple Name
                if isinstance(fn.value, ast.Name):
                    parent_name = fn.value.id
                else:
                    parent_name = ""  # unsupported complex expression
                child_name = ""
                if node.args:
                    if isinstance(node.args[0], ast.Name):
                        child_name = node.args[0].id
                prefix = ""
                for kw in node.keywords or []:
                    if kw.arg == "prefix":
                        prefix = _const_str(kw.value) or ""
                if parent_name and child_name:
                    edges.append((parent_name, child_name, prefix))
            self.generic_visit(node)

    V().visit(tree)
    # Dedup while preserving order
    apps = list(dict.fromkeys(apps))
    routers = list(dict.fromkeys(routers))
    return apps, routers, edges


def _compute_router_prefixes(apps: List[str], routers: List[str], edges: List[Tuple[str, str, str]]) -> Dict[str, str]:
    """
    Compute full prefix per router by walking parent relations up to an app.
    If multiple parents exist, prefer lexicographically smallest deterministic chain.
    """
    parent_map: Dict[str, List[Tuple[str, str]]] = {}  # child -> [(parent, prefix)]
    for parent, child, prefix in edges:
        parent_map.setdefault(child, []).append((parent, prefix or ""))

    cache: Dict[str, str] = {}

    def resolve(name: str, seen: Tuple[str, ...] = ()) -> str:
        if name in cache:
            return cache[name]
        # Base cases
        if name in apps:  # app has no prefix
            cache[name] = ""
            return ""
        if name not in parent_map:
            cache[name] = ""
            return ""
        # Choose a deterministic parent (lexicographically by parent name, then prefix)
        candidates = sorted(parent_map[name], key=lambda t: (t[0], t[1]))
        for parent, pref in candidates:
            if parent in seen:
                continue  # avoid cycles
            base = resolve(parent, seen + (name,))
            cache[name] = (base + (pref or ""))
            return cache[name]
        cache[name] = ""
        return ""
    # Compute for all routers
    for r in routers:
        resolve(r)
    return cache


def _collect_decorated_routes(tree: ast.AST,
                              apps: List[str],
                              routers: List[str]) -> List[Tuple[_DecoratedRoute, ast.FunctionDef]]:
    """
    Collect all function defs decorated with @<owner>.<method>("/path", ...).
    Returns pairs of parsed route and the underlying FunctionDef.
    """
    out: List[Tuple[_DecoratedRoute, ast.FunctionDef]] = []

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._handle_func(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._handle_func(node)
            self.generic_visit(node)

        def _handle_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                # Expect Attribute(Name(owner), method)
                if not isinstance(dec.func, ast.Attribute):
                    continue
                method = dec.func.attr
                if method not in _METHODS:
                    continue
                owner = ""
                if isinstance(dec.func.value, ast.Name):
                    owner = dec.func.value.id
                
                # Debug print
                # print(f"DEBUG: Found potential route decorator: @{owner}.{method} on {node.name}")
                
                if not owner or (owner not in apps and owner not in routers):
                    continue
                # First arg should be the path literal
                route_path = ""
                if dec.args:
                    route_path = _const_str(dec.args[0]) or ""
                if not route_path:
                    continue  # skip non-literal paths for determinism/size

                # Extract interesting kwargs
                response_model = None
                tags: List[str] = []
                dependencies: List[str] = []
                for kw in dec.keywords or []:
                    if kw.arg == "response_model":
                        rm = _extract_response_model(kw.value)
                        if rm:
                            response_model = rm
                    elif kw.arg == "tags":
                        tags = _extract_tags(kw.value)
                    elif kw.arg == "dependencies":
                        dependencies = _extract_dependencies(kw.value)

                out.append((
                    _DecoratedRoute(
                        owner=owner,
                        method=method.upper(),
                        path=route_path,
                        ln=node.lineno,
                        response_model=response_model,
                        tags=tags,
                        dependencies=dependencies,
                        func_name=node.name,
                    ),
                    node,
                ))
            self.generic_visit(node)

    V().visit(tree)
    # Deterministic order
    out.sort(key=lambda t: (t[0].owner, t[0].path, t[0].method, t[0].ln))
    return out


def analyze(cfg: Config, py_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Emit a single shard:
      {
        "routes": [
          {
            "path": "/api/x",
            "method": "GET",
            "handler": {"pid": 42, "qualname": "pkg.mod.fn"},
            ...
          }
        ]
      }
    """
    all_apps: List[str] = []
    all_routers: List[str] = []
    all_edges: List[Tuple[str, str, str]] = []

    # First pass: collect all apps, routers, and their prefix edges across all files
    trees: Dict[Path, ast.AST] = {}
    for path in py_files:
        tree = ast_utils.parse_tree(path)
        trees[path] = tree
        apps, routers, edges = _collect_apps_and_routers(tree)
        if apps or routers:
            print(f"DEBUG: Found apps={apps}, routers={routers} in {path}")
        all_apps.extend(apps)
        all_routers.extend(routers)
        all_edges.extend(edges)

    # Dedup
    all_apps = list(dict.fromkeys(all_apps))
    all_routers = list(dict.fromkeys(all_routers))
    print(f"DEBUG: Global apps={all_apps}, routers={all_routers}")

    # Compute global prefix map
    prefix_map = _compute_router_prefixes(all_apps, all_routers, all_edges)

    # Second pass: collect all routes across all files using the discovered apps/routers
    routes_out: List[Dict[str, Any]] = []
    for path, tree in trees.items():
        pid = pid_by_path.get(path)
        if pid is None:
            continue

        module = ast_utils.module_name_from_path(path, cfg.root)
        decorated = _collect_decorated_routes(tree, all_apps, all_routers)
        
        for route, fn in decorated:
            prefix = prefix_map.get(route.owner, "") if route.owner in all_routers else ""
            full_path = (prefix + route.path) if prefix else route.path

            req_model = _first_param_model(fn)
            routes_out.append({
                "path": full_path,
                "method": route.method,
                "handler": {"pid": pid, "qualname": f"{module}.{route.func_name}"},
                "request_model": req_model,
                "response_model": route.response_model,
                "deps": route.dependencies,
                "tags": route.tags,
            })

    # Deterministic sort
    routes_out.sort(key=lambda r: (r["path"], r["method"], r["handler"]["qualname"]))

    return {"routes": routes_out}
