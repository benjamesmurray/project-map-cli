# utils/digest_tool_v3/analyzers/pydantic_models.py
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..common import ast_utils, hashing

# Spec cap (if not present on Config). Keep small to bound shard size.
_DEFAULT_MAX_FIELDS_PER_MODEL = 120


def _is_basemodel_subclass(node: ast.ClassDef) -> bool:
    """
    True if any base is 'BaseModel' or endswith '.BaseModel' (pydantic v1/v2).
    """
    for b in node.bases:
        name = ast_utils._attr_to_str(b) or ""
        if name.endswith("BaseModel"):
            return True
    return False


def _annotation_str(ann: ast.AST) -> str:
    """
    Best-effort stringify for type annotations.
    Prefer ast.unparse when available; else fall back to attr strings or dumps.
    """
    try:
        # py>=3.9
        return ast.unparse(ann)  # type: ignore[attr-defined]
    except Exception:
        s = ast_utils._attr_to_str(ann)
        if s:
            return s
        try:
            return ast.dump(ann, include_attributes=False)
        except Exception:
            return str(type(ann).__name__)


def _is_field_call(val: ast.AST) -> bool:
    """
    Detect pydantic Field(...) calls.
    """
    return isinstance(val, ast.Call) and (ast_utils._attr_to_str(val.func) or "").endswith("Field")


def _gather_fields_from_class(cls: ast.ClassDef) -> List[Dict[str, Any]]:
    """
    Extract fields from AnnAssign nodes in the class body.
    - name: identifier
    - type: annotation (stringified)
    - required: bool (no default present)
    - default_present: bool (value present OR Field(...) present)
    """
    fields: List[Dict[str, Any]] = []
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            name = stmt.target.id
            ann = stmt.annotation
            type_str = _annotation_str(ann) if ann is not None else ""
            default_present = stmt.value is not None or (stmt.value is not None and _is_field_call(stmt.value))
            # If value exists and is Field(...), default_present is True regardless of args.
            if stmt.value is not None and _is_field_call(stmt.value):
                default_present = True
            fields.append({
                "name": name,
                "type": type_str,
                "required": not bool(default_present),
                "default_present": bool(default_present),
            })
        # Ignore non-annotated Assigns; too noisy / ambiguous for static analysis.
    # Deterministic order
    fields.sort(key=lambda f: f["name"])
    return fields


def analyze(cfg: Config, py_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Emit:
      {
        "models": [
          {
            "name": "SceneResponse",
            "module": "viz_portal.backend.models.scene",
            "hash": "sha256:...",
            "fields": [ {name,type,required,default_present}, ... ],
            "truncated": true|false
          },
          ...
        ]
      }
    """
    models_out: List[Dict[str, Any]] = []
    max_fields = getattr(cfg, "max_fields_per_model", _DEFAULT_MAX_FIELDS_PER_MODEL)

    for path in py_files:
        module = ast_utils.module_name_from_path(path, cfg.root)
        try:
            tree = ast_utils.parse_tree(path)
        except Exception:
            continue

        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not _is_basemodel_subclass(node):
                continue

            fields = _gather_fields_from_class(node)
            truncated = False
            if len(fields) > max_fields:
                fields = fields[:max_fields]
                truncated = True

            schema_core = {
                "name": node.name,
                "module": module,
                "fields": fields,
            }
            schema_hash = hashing.stable_hash(schema_core)

            models_out.append({
                **schema_core,
                "hash": schema_hash,
                "truncated": truncated,
            })

    # Deterministic sort by module then name
    models_out.sort(key=lambda m: (m["module"], m["name"]))

    return {"models": models_out}
