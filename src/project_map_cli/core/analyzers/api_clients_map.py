# utils/digest_tool_v3/analyzers/api_clients_map.py
from __future__ import annotations

from typing import Any, Dict, List


def _route_key(path: str, method: str) -> str:
    return f"{path}#{(method or '').upper()}"


def analyze(cfg, api_routes_doc: Dict[str, Any], fe_calls_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Join FastAPI routes and FE callsites by normalized (path, method).

    Inputs:
      api_routes_doc = {"routes":[{"path":..., "method":..., "handler":{"pid":...,"qualname":...}, ...}, ...]}
      fe_calls_doc   = {"calls":[{"url":..., "method":..., "pid":..., "component":..., ...}, ...]}

    Output:
      {
        "links": [
          {"route": "/api/scene#GET", "clients": [{"pid": 301, "component": "SceneView.vue"}]},
          ...
        ]
      }
    """
    # Build route index
    routes = api_routes_doc.get("routes", []) or []
    calls = fe_calls_doc.get("calls", []) or []

    route_index: Dict[str, Dict[str, Any]] = {}
    for r in routes:
        path = str(r.get("path", "") or "")
        method = str(r.get("method", "") or "GET").upper()
        if not path:
            continue
        key = _route_key(path, method)
        # Keep first occurrence deterministically; duplicates will collapse
        if key not in route_index:
            route_index[key] = r

    # Accumulate clients per route
    clients_by_route: Dict[str, List[Dict[str, Any]]] = {}
    for c in calls:
        url = str(c.get("url", "") or "")
        method = str(c.get("method", "") or "GET").upper()
        if not url:
            continue
        key = _route_key(url, method)
        if key not in route_index:
            # FE call to a URL that doesn't match any known FastAPI route — ignore for this map
            continue
        clients_by_route.setdefault(key, []).append({
            "pid": int(c.get("pid", -1)),
            "component": str(c.get("component", "") or ""),
        })

    # Build output list; sort deterministically
    links: List[Dict[str, Any]] = []
    for key in sorted(clients_by_route.keys()):
        clients = clients_by_route[key]
        # Sort clients by component asc, then pid asc
        clients.sort(key=lambda cl: (cl["component"], cl["pid"]))
        links.append({"route": key, "clients": clients})

    return {"links": links}
