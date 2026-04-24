# utils/digest_tool_v2/common/ast_utils.py
from __future__ import annotations

import ast
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ------------------------------- public API ----------------------------------

def module_name_from_path(path: Path, root: Path) -> str:
    """
    Best-effort dotted module name from a file path under `root`.
    Examples:
      root=/repo, path=/repo/pkg/mod.py  -> 'pkg.mod'
      root=/repo, path=/repo/app/__init__.py -> 'app'
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except Exception:
        rel = path.name  # fallback
        return str(rel).replace("/", ".").replace("\\", ".").removesuffix(".py")

    parts = list(rel.parts)
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def parse_tree(path: Path) -> ast.AST:
    """
    Parse a Python source file into an AST with per-node positions.
    Cached per absolute path content to avoid re-reading.
    Returns an empty Module if parsing fails.
    """
    src = _read_text_cached(path)
    try:
        # Use type comments enabled; feature_version left to default (current runtime)
        return ast.parse(src, filename=str(path))
    except (SyntaxError, ValueError) as exc:
        import sys
        sys.stderr.write(f"[digest_tool_v3] ERROR: {exc} ({path.name}, line {getattr(exc, 'lineno', '?')})\n")
        return ast.Module(body=[], type_ignores=[])


def get_module_docstring(path: Path) -> bool:
    """Return True if the module has a docstring."""
    tree = parse_tree(path)
    return ast.get_docstring(tree) is not None


def list_imports(path: Path) -> List[Dict[str, Any]]:
    """
    Extract imports in compressed form for files_index:
      - k: kind ('imp' | 'from')
      - mod: module (as written; '' for 'import x as y' entries becomes the top-level name)
      - n: imported name or alias (for 'from' it's the name; for 'import' it's the alias/name)
      - ln: line number
    Deterministic ordering by source order (AST walk preserves node order).
    """
    tree = parse_tree(path)
    out: List[Dict[str, Any]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append({
                    "k": "imp",
                    "mod": alias.name,
                    "n": alias.asname or alias.name,
                    "ln": node.lineno,
                })
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                out.append({
                    "k": "from",
                    "mod": mod,
                    "n": alias.asname or alias.name,
                    "ln": node.lineno,
                })

    return out


def list_defs(path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Return (classes, functions, methods).
    Each item is { "name": str, "ln": int, "doc": 0|1 }.
    Methods include {"class": ClassName, ...}.
    """
    tree = parse_tree(path)
    classes: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []
    methods: List[Dict[str, Any]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            classes.append({"name": node.name, "ln": node.lineno, "doc": 1 if _has_doc(node) else 0})
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({
                        "class": node.name,
                        "name": b.name,
                        "ln": b.lineno,
                        "doc": 1 if _has_doc(b) else 0,
                    })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({"name": node.name, "ln": node.lineno, "doc": 1 if _has_doc(node) else 0})

    # Deterministic order: by line number, then name
    classes.sort(key=lambda d: (d["ln"], d["name"]))
    functions.sort(key=lambda d: (d["ln"], d["name"]))
    methods.sort(key=lambda d: (d["ln"], d.get("class", ""), d["name"]))
    return classes, functions, methods


def detect_entry_points(path: Path) -> Dict[str, Any]:
    """
    Detect __main__ guard and argparse-style CLIs in the module.
    Returns:
      {
        "has_main_guard": bool,
        "main_lines": [int, ...],
        "argparse_calls": [int, ...]
      }
    """
    tree = parse_tree(path)
    result = {
        "has_main_guard": False,
        "main_lines": [],        # line numbers of if-__name__ guards
        "argparse_calls": [],    # line numbers where argparse.ArgumentParser is called
    }

    class Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if _is_main_guard(node.test):
                result["has_main_guard"] = True
                result["main_lines"].append(node.lineno)
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            # argparse.ArgumentParser(...)
            fn = _attr_to_str(node.func)
            if fn == "argparse.ArgumentParser":
                result["argparse_calls"].append(node.lineno)
            self.generic_visit(node)

    Visitor().visit(tree)
    # Deterministic sort
    result["main_lines"].sort()
    result["argparse_calls"].sort()
    return result


def list_ctor_calls(path: Path, *, include_snippet: bool = True, max_snippet_chars: int = 140) -> List[Dict[str, Any]]:
    """
    Extract constructor-like call sites (Name(...) or dotted Attribute(...)(...)).
    Returns list of:
      {
        "kind": "name" | "attr",
        "callee": "ClassName" | "pkg.mod.ClassName",
        "ln": int,
        "args": int,                     # positional count
        "kwargs": [str, ...],            # keyword arg names
        "snippet": "..."                 # trimmed, optional
      }
    This does NOT resolve to actual repo classes; resolution is done in analyzers.
    """
    src = _read_text_cached(path)
    lines = src.splitlines()
    tree = parse_tree(path)
    calls: List[Dict[str, Any]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            callee_kind, callee = _callable_name(node.func)
            if callee is None:
                self.generic_visit(node)
                return
            args_n = sum(1 for a in node.args if not isinstance(a, ast.Starred))
            kw_names = [k.arg for k in node.keywords if k.arg is not None]
            rec: Dict[str, Any] = {
                "kind": callee_kind,
                "callee": callee,
                "ln": node.lineno,
                "args": int(args_n),
                "kwargs": kw_names,
            }
            if include_snippet:
                rec["snippet"] = _line_snippet(lines, node.lineno, max_snippet_chars)
            calls.append(rec)
            self.generic_visit(node)

    Visitor().visit(tree)
    calls.sort(key=lambda d: (d["ln"], d["callee"]))
    return calls


# ------------------------------ internal utils -------------------------------

def _has_doc(node: ast.AST) -> bool:
    try:
        return ast.get_docstring(node) is not None
    except Exception:
        return False


def _is_main_guard(test: ast.expr) -> bool:
    """
    Match: if __name__ == "__main__":
    """
    # Allow both sides being reversed and both single/double quotes
    def _literal_str(e: ast.AST) -> Optional[str]:
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return e.value
        return None

    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
        left, op, right = test.left, test.ops[0], test.comparators[0]
        if not isinstance(op, (ast.Eq, ast.Is)):
            return False
        left_s = _literal_str(left)
        right_s = _literal_str(right)
        # __name__ == "__main__"  OR  "__main__" == __name__
        if isinstance(left, ast.Name) and left.id == "__name__" and right_s == "__main__":
            return True
        if isinstance(right, ast.Name) and right.id == "__name__" and left_s == "__main__":
            return True
    return False


def _attr_to_str(node: ast.AST) -> Optional[str]:
    """
    Turn Attribute chains into dotted strings: pkg.mod.Name
    Returns None if not resolvable.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _attr_to_str(node.value)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def _callable_name(func: ast.expr) -> Tuple[str, Optional[str]]:
    """
    If callable is a Name or Attribute chain, return ("name"/"attr", dotted).
    Else (subscript, lambda, call result, etc.) return (kind, None).
    """
    if isinstance(func, ast.Name):
        return "name", func.id
    dotted = _attr_to_str(func)
    if dotted is not None:
        return "attr", dotted
    return "other", None


@lru_cache(maxsize=4096)
def _read_text_cached(path: Path) -> str:
    """
    Cache file text by absolute path; speeds up multi-analyzer runs.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    # Normalize indentation quirks to help snippet extraction
    return textwrap.dedent(text)


def _line_snippet(lines: List[str], lineno: int, max_chars: int) -> str:
    if 1 <= lineno <= len(lines):
        s = lines[lineno - 1].strip()
    else:
        s = ""
    if len(s) > max_chars:
        s = s[: max_chars - 1] + "…"
    return s
