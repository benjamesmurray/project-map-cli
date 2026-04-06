# infra/digest_tool_v6/common/graph_serialization.py
from __future__ import annotations

import json
import networkx as nx
from typing import Any, Dict, List

def serialize_graph_to_edge_list(graph: nx.DiGraph, version: str = "1.0.0", shard_id: str = "A") -> Dict[str, Any]:
    """
    Serializes a NetworkX DiGraph to a strict Edge-List JSON schema.
    Format:
    {
      "metadata": { "shard_id": str, "version": str },
      "edges": {
        "caller_fqn": [
          { "dst": "callee_fqn", "pid": int, "ln": int },
          ...
        ],
        ...
      }
    }
    """
    edges_out = {}
    for src, dst, data in graph.edges(data=True):
        if src not in edges_out:
            edges_out[src] = []
        
        edge_entry = {
            "dst": dst,
            "pid": data.get("pid", -1),
            "ln": data.get("ln", -1)
        }
        edges_out[src].append(edge_entry)
        
    return {
        "metadata": {
            "shard_id": shard_id,
            "version": version
        },
        "edges": edges_out
    }

def serialize_metadata(version: str, generated_at: str, capabilities: List[str], gsi: Dict[str, str]) -> Dict[str, Any]:
    """
    Serializes metadata.json including the Global Symbol Index.
    """
    return {
        "version": version,
        "generated_at": generated_at,
        "capabilities": capabilities,
        "global_symbol_index": gsi
    }
