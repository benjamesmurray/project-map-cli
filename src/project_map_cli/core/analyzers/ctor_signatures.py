# utils/digest_tool_v3/analyzers/ctor_signatures.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Set

from ..config import Config
from ..common import ast_utils, filters


@dataclass(frozen=True)
class _CallSite:
    pid: int
    ln: int
    snippet: str


def _basename(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1] if dotted else ""


# --- Leaf-level deny lists (robust against qualified names) -------------------

# Common Python builtins and helpers that show up as calls but are not constructors.
_BUILTIN_CALLS: Set[str] = {
    "len", "range", "print", "sorted", "sum", "min", "max", "any", "all",
    "map", "filter", "zip", "enumerate", "reversed", "next", "iter",
    "list", "dict", "set", "tuple", "bool", "int", "float", "str", "bytes",
    "bytearray", "object", "type", "super", "abs", "round",
    "getattr", "setattr", "hasattr", "delattr",
    "isinstance", "issubclass",
    "open",
}

# Common stdlib decorators/helpers we never want as "constructors".
_STDLIB_HELPERS: Set[str] = {
    # dataclasses
    "dataclass", "field", "asdict", "astuple", "make_dataclass", "replace",
    # typing helpers often appear in calls
    "cast", "overload", "NewType",
}

# Common stdlib types/classes frequently called but not meaningful ctor signals.
_STDLIB_TYPES: Set[str] = {
    # datetime
    "datetime", "timedelta", "date", "time", "timezone",
    # pathlib
    "Path", "PurePath", "PosixPath", "WindowsPath",
    # collections
    "deque", "defaultdict", "Counter", "OrderedDict",
    # builtin-ish types often surfaced
    "frozenset", "Decimal",
    # uuid
    "UUID",
}

# Common exception class names (and pattern: *Error) that aren’t meaningful ctor signals.
_EXCEPTION_NAMES: Set[str] = {
    "Exception", "BaseException",
    "AssertionError", "AttributeError", "EOFError", "FloatingPointError",
    "GeneratorExit", "ImportError", "ModuleNotFoundError", "IndexError",
    "KeyError", "KeyboardInterrupt", "MemoryError", "NameError",
    "NotImplementedError", "OSError", "IOError", "OverflowError",
    "RecursionError", "ReferenceError", "RuntimeError", "StopIteration",
    "StopAsyncIteration", "SyntaxError", "IndentationError", "TabError",
    "SystemError", "SystemExit", "TypeError", "UnboundLocalError",
    "UnicodeError", "UnicodeDecodeError", "UnicodeEncodeError",
    "UnicodeTranslateError", "ValueError", "ZeroDivisionError",
    "TimeoutError", "BrokenPipeError", "ConnectionError",
    "FileNotFoundError", "PermissionError",
}


def _leaf_drop_reason(symbol: str) -> Optional[str]:
    """
    Decide if a resolved qualified name should be dropped based on the *leaf*.
    Returns one of:
      'builtin_leaf' | 'stdlib_helper' | 'stdlib_type' | 'exception_leaf' | 'no_leaf'
    or None to keep.
    """
    leaf = _basename(symbol)
    if not leaf:
        return "no_leaf"

    # Obvious non-constructors (builtin functions, helpers)
    if leaf in _BUILTIN_CALLS:
        return "builtin_leaf"

    # Stdlib helpers/decorators that aren’t constructors
    if leaf in _STDLIB_HELPERS:
        return "stdlib_helper"

    # Stdlib types/classes we treat as noise
    if leaf in _STDLIB_TYPES:
        return "stdlib_type"

    # Exception classes
    if leaf in _EXCEPTION_NAMES or leaf.endswith("Error"):
        return "exception_leaf"

    return None


# --- Resolution ----------------------------------------------------------------

def _resolve_qualname(module_name: str, rec: Mapping[str, Any]) -> Tuple[Optional[str], str]:
    """
    Best-effort resolution of a constructor-like callsite to a qualified name.

    Returns (qualname|None, reason)
      - reason is 'ok' when resolved, else a short drop reason for telemetry.
    """
    kind = rec.get("kind")
    callee = rec.get("callee") or ""
    if not callee:
        return None, "no_callee"

    if kind == "name":
        # Treat bare Name(...) as a class/function in the same module
        # e.g. Foo(...) inside pkg.mod -> pkg.mod.Foo
        if filters.is_builtin_call(callee):
            return None, "builtin"  # classic builtin by bare name
        # Local symbol assumption
        return f"{module_name}.{callee}", "ok"

    if kind == "attr":
        # Dotted attribute like pkg.mod.Foo or alias.Foo
        # If the last segment is a banned attr name (get/keys/etc.), drop.
        if filters.omit_attr_name(_basename(callee)):
            return None, "banned_attr"
        # Accept dotted path as-is
        return callee, "ok"

    return None, "unsupported"


def _safe_module_token(module_name: str) -> str:
    """
    File-safe token for per-module shard filenames.
    Keep dotted module to be readable; avoid path separators.
    """
    return module_name or "__unknown__"


# --- Public API ----------------------------------------------------------------

def analyze(cfg: Config, py_files: List[Path], pid_by_path: Dict[Path, int]) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """
    Build constructor callsite aggregates.

    Returns:
      ctor_top_doc: {
        "total_calls": int,
        "symbols": [ { "symbol": str, "total": int }, ... ]  # sorted desc, capped by cfg.max_top_symbols
        "dropped": {
            "builtin": int, "banned_attr": int, "no_callee": int, "unsupported": int, "ns_excluded": int,
            "builtin_leaf": int, "stdlib_helper": int, "stdlib_type": int, "exception_leaf": int, "no_leaf": int
        }
      }

      ctor_items: {
        "<module_name>": {
          "module": "<module_name>",
          "symbols": [
            {
              "symbol": "pkg.mod.Foo",
              "total": 7,
              "samples": [ { "pid": 12, "ln": 98, "snippet": "Foo(x, y)" }, ... ]  # up to cfg.max_callsites
            },
            ...
          ]
        },
        ...
      }
    """
    # Aggregates
    per_symbol_total: Dict[str, int] = defaultdict(int)
    per_module_symbol_calls: Dict[str, Dict[str, List[_CallSite]]] = defaultdict(lambda: defaultdict(list))
    dropped: Dict[str, int] = defaultdict(int)

    total_calls = 0

    # Deterministic pass (py_files already sorted)
    for path in py_files:
        pid = pid_by_path.get(path)
        if pid is None:
            continue

        module_name = ast_utils.module_name_from_path(path, cfg.root)
        calls = ast_utils.list_ctor_calls(path, include_snippet=True)

        for c in calls:
            total_calls += 1

            qualname, reason = _resolve_qualname(module_name, c)
            if qualname is None:
                dropped[reason] += 1
                continue

            # Leaf-based drop for builtins/helpers/exceptions/stdlib types even when qualified
            leaf_reason = _leaf_drop_reason(qualname)
            if leaf_reason is not None:
                dropped[leaf_reason] += 1
                continue

            # Namespace filtering: keep only repo symbols (if regex is present)
            if cfg.ns_allow_re is not None and not filters.should_keep_ctor_symbol(qualname, cfg.ns_allow_re):
                dropped["ns_excluded"] += 1
                continue

            # Record
            per_symbol_total[qualname] += 1
            per_module_symbol_calls[module_name][qualname].append(
                _CallSite(pid=pid, ln=int(c.get("ln", 0) or 0), snippet=str(c.get("snippet", "")))
            )

    # Build top doc
    symbols_sorted = sorted(
        ({"symbol": s, "total": n} for s, n in per_symbol_total.items()),
        key=lambda d: (-d["total"], d["symbol"]),
    )
    ctor_top_doc: Dict[str, Any] = {
        "total_calls": total_calls,
        "symbols": symbols_sorted[: cfg.max_top_symbols],
        "dropped": dict(sorted(dropped.items())),
    }

    # Build per-module shards
    ctor_items: Dict[str, Dict[str, Any]] = {}
    for module_name, sym_map in sorted(per_module_symbol_calls.items(), key=lambda kv: kv[0]):
        rows: List[Dict[str, Any]] = []
        for symbol, sites in sorted(sym_map.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            # Deterministic order of samples by (ln, pid)
            sites_sorted = sorted(sites, key=lambda s: (s.ln, s.pid))
            samples = [{"pid": s.pid, "ln": s.ln, "snippet": s.snippet} for s in sites_sorted[: cfg.max_callsites]]
            rows.append({
                "symbol": symbol,
                "total": len(sites),
                "samples": samples,
                # We intentionally do not attempt signature reflection statically in v3.
                "sig_unknown": True,
            })
        ctor_items[_safe_module_token(module_name)] = {
            "module": module_name,
            "symbols": rows,
        }

    return ctor_top_doc, ctor_items
