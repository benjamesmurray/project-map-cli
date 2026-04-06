# utils/digest_tool_v3/analyzers/kotlin_symbols.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..common import tree_sitter_util as tsu


# -------------------------
# Hot-file detection (light)
# -------------------------

_RE_STREAM_CALL = re.compile(r"\.stream\s*\(", re.MULTILINE)
_RE_KAFKA_IMPORT = re.compile(r"org\.apache\.kafka\.streams", re.MULTILINE)


def _is_hot_kotlin(text: str) -> bool:
    if not text:
        return False
    return bool(_RE_KAFKA_IMPORT.search(text) or _RE_STREAM_CALL.search(text))


# -------------------------
# Text helpers
# -------------------------

_RE_ANN = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")

# Best-effort “header” extraction: everything up to first '{' (or whole text if none).
_RE_HEADER_SPLIT = re.compile(r"\{", re.MULTILINE)

# Kotlin supertypes are after ':' in the header.
_RE_SUPERTYPES = re.compile(r":\s*(?P<rhs>[^({\n]+(?:\([^\)]*\))?[^({\n]*)", re.MULTILINE)

# Pull “type-ish” tokens from a supertype chunk, capturing last segment of qualified name.
_RE_TYPE_NAME = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\s*<|\s*\(|\s*$)")


def _extract_annotation_names(snippet: str) -> List[str]:
    """
    Best-effort: return ["Serializable", "JvmStatic", "com.foo.Bar"] from any "@..." occurrences.
    Deterministic: preserve first-seen order but de-dup.
    """
    out: List[str] = []
    seen: set[str] = set()
    for m in _RE_ANN.finditer(snippet or ""):
        name = m.group(1)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _ln_from_node(node: Any) -> int:
    # Tree-sitter start_point is (row, col), 0-based
    sp = getattr(node, "start_point", (0, 0)) or (0, 0)
    try:
        return int(sp[0]) + 1
    except Exception:
        return 1


def _first_named_child_text(node: Any, source: bytes, types: Tuple[str, ...]) -> str:
    """
    Find first named child with type in `types`, return its text. Else "".
    """
    try:
        for ch in getattr(node, "named_children", []) or []:
            if getattr(ch, "type", "") in types:
                return tsu.node_text(ch, source).strip()
    except Exception:
        return ""
    return ""


def _find_identifier_in_node(node: Any, source: bytes) -> str:
    """
    Kotlin grammar differences across packages are real. This tries common identifier node types.
    """
    # common in tree-sitter-kotlin:
    # - simple_identifier
    # - identifier
    # - type_identifier
    for t in ("simple_identifier", "identifier", "type_identifier"):
        nm = _first_named_child_text(node, source, (t,))
        if nm:
            return nm

    # fallback: scan children for something that looks like an identifier token
    try:
        for ch in getattr(node, "named_children", []) or []:
            txt = tsu.node_text(ch, source).strip()
            if txt and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", txt):
                return txt
    except Exception:
        pass

    return ""


def _header_text(decl_text: str) -> str:
    """
    Return the declaration header (up to first '{') to avoid pulling in method bodies.
    """
    if not decl_text:
        return ""
    parts = _RE_HEADER_SPLIT.split(decl_text, maxsplit=1)
    return (parts[0] if parts else decl_text).strip()


def _extract_implements_list(decl_text: str) -> List[str]:
    """
    Best-effort extraction of implements/extends types from a class/enum/interface header.

    Kotlin examples:
      class X(...) : Processor<String, String, ...> { ... }
      class Y : Foo, Bar<Baz> { ... }
      enum class Route : Something { EVENTS, DLQ }

    Output is simple type names (last segment if qualified), de-duped, first-seen order.
    """
    hdr = _header_text(decl_text)
    if ":" not in hdr:
        return []

    m = _RE_SUPERTYPES.search(hdr)
    if not m:
        return []

    rhs = (m.group("rhs") or "").strip()
    if not rhs:
        return []

    # Split on commas at top level (we don't try to be clever about nested generics).
    raw_parts = [p.strip() for p in rhs.split(",") if p.strip()]

    out: List[str] = []
    seen: set[str] = set()

    for part in raw_parts:
        # Drop trailing constructor calls / delegation bits after first whitespace + "by" patterns etc.
        # Keep it simple: identify the first type-ish token and take its last qualified segment.
        # If qualified, split by '.' and keep last.
        # Example: org.apache.kafka.streams.processor.api.Processor<...> -> Processor
        token = part.strip()

        # Remove "by ..." delegation tail if present
        if " by " in token:
            token = token.split(" by ", 1)[0].strip()

        # If qualified, keep last segment for the first type name
        # Find the first type-ish occurrence
        tmatch = _RE_TYPE_NAME.search(token)
        if not tmatch:
            continue

        tname = tmatch.group(1) or ""
        if not tname:
            continue

        # If token itself starts with qualified prefix, last segment still ends up captured as tname
        # but we also guard for cases where token begins with "a.b.C<...>" where regex captures "a"
        # So: if there's a dot, take last segment before any '<' or '('.
        if "." in token:
            head = token.split("<", 1)[0].split("(", 1)[0].strip()
            last = head.split(".")[-1].strip()
            if last and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", last):
                tname = last

        if tname not in seen:
            seen.add(tname)
            out.append(tname)

    return out


# -------------------------
# Symbol walk
# -------------------------

@dataclass(frozen=True)
class _Ctx:
    package: str
    stack: Tuple[str, ...]
    enum_container: Optional[str]


def _qname(pkg: str, stack: Tuple[str, ...], name: str) -> str:
    parts: List[str] = []
    if pkg:
        parts.append(pkg)
    parts.extend([p for p in stack if p])
    if name:
        parts.append(name)
    return ".".join(parts) if parts else name


def _emit_symbol(
    out: List[Dict[str, Any]],
    *,
    name: str,
    kind: str,
    pid: int,
    ln: int,
    qname: str,
    annotations: Optional[List[str]] = None,
    implements: Optional[List[str]] = None,
    container: Optional[str] = None,
) -> None:
    row: Dict[str, Any] = {
        "name": name,
        "kind": kind,
        "pid": pid,
        "ln": ln,
        "qname": qname,
    }
    if annotations is not None:
        row["annotations"] = annotations
    if implements is not None:
        row["implements"] = implements
    if container is not None:
        row["container"] = container
    out.append(row)


def _walk(
    node: Any,
    source: bytes,
    pid: int,
    cfg: Config,
    ctx: _Ctx,
    out: List[Dict[str, Any]],
    symbols_cap: int,
) -> None:
    if len(out) >= symbols_cap:
        return

    ntype = getattr(node, "type", "") or ""

    is_enum_class = ntype == "enum_class_declaration"
    is_class_decl = ntype in (
        "class_declaration", 
        "object_declaration", 
        "interface_declaration", 
        "companion_object"
    )
    is_classlike = is_enum_class or is_class_decl
    is_function = ntype == "function_declaration"
    is_enum_entry = ntype == "enum_entry"

    # --- Enum entries ---
    if is_enum_entry:
        name = _find_identifier_in_node(node, source)
        if name:
            container = ctx.enum_container
            qn = _qname(ctx.package, ctx.stack, name)
            _emit_symbol(
                out,
                name=name,
                kind="enum_entry",
                pid=pid,
                ln=_ln_from_node(node),
                qname=qn,
                container=container,
            )
        # enum_entry usually has no nested decls we care about, but still walk children defensively
        for ch in getattr(node, "named_children", []) or []:
            _walk(ch, source, pid, cfg, ctx, out, symbols_cap)
        return

    # --- Class-like declarations (class/object/interface/enum class) ---
    if is_classlike:
        decl_text = tsu.node_text(node, source)
        name = _find_identifier_in_node(node, source)
        if not name and ntype == "companion_object":
            name = "Companion"

        if name:
            ann = _extract_annotation_names(_header_text(decl_text)[:8000])  # bounded
            impl = _extract_implements_list(decl_text)

            kind = "enum" if is_enum_class else "class"

            qn = _qname(ctx.package, ctx.stack, name)
            _emit_symbol(
                out,
                name=name,
                kind=kind,
                pid=pid,
                ln=_ln_from_node(node),
                qname=qn,
                annotations=ann,
                implements=impl,
            )

            next_ctx = _Ctx(
                package=ctx.package,
                stack=ctx.stack + (name,),
                enum_container=name if is_enum_class else None,
            )
        else:
            next_ctx = ctx

        for ch in getattr(node, "named_children", []) or []:
            _walk(ch, source, pid, cfg, next_ctx, out, symbols_cap)
        return

    # --- Function / method declarations ---
    if is_function:
        decl_text = tsu.node_text(node, source)
        name = _find_identifier_in_node(node, source)
        if name:
            # Minimal requirement: record method declarations for process/init.
            # We record all, but you can cheaply filter downstream.
            kind = "method" if ctx.stack else "function"
            ann = _extract_annotation_names(_header_text(decl_text)[:4000])  # bounded
            qn = _qname(ctx.package, ctx.stack, name)
            _emit_symbol(
                out,
                name=name,
                kind=kind,
                pid=pid,
                ln=_ln_from_node(node),
                qname=qn,
                annotations=ann,
            )

            next_ctx = _Ctx(
                package=ctx.package,
                stack=ctx.stack + (name,),
                enum_container=None,
            )
        else:
            next_ctx = ctx

        # Walk into function bodies to find nested named symbols
        for ch in getattr(node, "named_children", []) or []:
            _walk(ch, source, pid, cfg, next_ctx, out, symbols_cap)
        return

    # Default: walk children
    for ch in getattr(node, "named_children", []) or []:
        _walk(ch, source, pid, cfg, ctx, out, symbols_cap)


def _parse_package_and_imports(root_node: Any, source: bytes) -> Tuple[str, List[str]]:
    """
    Scans top-level children for package and import headers.
    """
    pkg = ""
    imports: List[str] = []

    try:
        children = getattr(root_node, "named_children", []) or []
        for ch in children:
            t = getattr(ch, "type", "") or ""
            # Stop if we hit a declaration, but imports can be many
            if t in ("class_declaration", "object_declaration", "function_declaration", "interface_declaration"):
                # Usually we can stop after declarations start, but let's be safe and scan a bit more
                # or just scan all top-level nodes since it's fast.
                pass

            txt = tsu.node_text(ch, source).strip()
            if not txt:
                continue

            if t == "package_header" or txt.startswith("package "):
                pkg = txt.replace("package", "").strip().rstrip(";").strip()
                continue

            if t == "import_header" or txt.startswith("import "):
                imp = txt.replace("import", "").strip().rstrip(";").strip()
                if imp:
                    imports.append(imp)
    except Exception:
        pass

    # Deterministic + de-dup, preserve order of first-seen
    seen: set[str] = set()
    out_imps: List[str] = []
    for i in imports:
        if i not in seen:
            seen.add(i)
            out_imps.append(i)

    return pkg, out_imps


def analyze(cfg: Config, kt_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Kotlin symbol table for navigation and LLM lookup.

    Adds (Step 2):
      - enum declarations + enum entries (Route.EVENTS, Route.DLQ)
      - implements/extends list for class declarations (identify Processor implementations)
      - method declarations (process/init discoverable by name)

    Output (deterministic, bounded):
      {
        "version": "3.0",
        "file_count": int,
        "symbols_count": int,
        "files": [
          {"pid": int, "path": str, "package": str, "imports": [...], "symbols": int, "truncated": bool, "error": {...}|null}
        ],
        "symbols": [
          {
            "name": str,
            "kind": "enum"|"enum_entry"|"class"|"function"|"method",
            "pid": int,
            "ln": int,
            "qname": str,
            "annotations": [...],          # optional
            "implements": [...],           # class/enum only
            "container": "Route"           # enum_entry only
          }
        ],
        "errors": [ ... ]
      }

    Notes:
      - In light profile, only "hot" Kotlin files are parsed (Kafka Streams import or `.stream(` usage).
      - Caps enforced here even if orchestrator already applies early limiting.
    """
    # Defensive defaults if cfg hasn’t been fully wired yet
    max_kotlin_files = int(getattr(cfg, "max_kotlin_files", 250) or 250)
    max_symbols_per_file = int(getattr(cfg, "max_kotlin_symbols_per_file", 500) or 500)

    errors: List[dict] = []
    files_out: List[Dict[str, Any]] = []
    symbols_out: List[Dict[str, Any]] = []

    # Load Kotlin language
    langr = tsu.load_kotlin_language()
    if not langr.ok:
        err = langr.error.to_dict() if langr.error else {"code": "UNKNOWN", "message": "unknown error"}
        return {
            "version": "3.0",
            "file_count": 0,
            "symbols_count": 0,
            "files": [],
            "symbols": [],
            "errors": [err],
        }
    lang = langr.value

    # Deterministic file order by repo-relative path
    root = cfg.root
    ordered = sorted(kt_files, key=lambda p: p.relative_to(root).as_posix())

    parsed_count = 0
    for path in ordered:
        if parsed_count >= max_kotlin_files:
            break

        pid = int(pid_by_path.get(path, -1))
        rel = path.relative_to(root).as_posix()

        # Light mode: hot-only gating
        try:
            text_probe = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text_probe = ""

        if getattr(cfg, "profile", "full") == "light" and not _is_hot_kotlin(text_probe):
            continue

        parsed_count += 1

        # Parse with tree-sitter
        pr = tsu.parse_file(lang, path)
        if not pr.ok:
            err = pr.error.to_dict() if pr.error else {"code": "PARSE_FAILED", "message": "parse failed"}
            files_out.append(
                {
                    "pid": pid,
                    "path": rel,
                    "package": "",
                    "imports": [],
                    "symbols": 0,
                    "truncated": False,
                    "error": err,
                }
            )
            errors.append(err)
            continue

        tree, source = pr.value
        root_node = getattr(tree, "root_node", None)

        if root_node is None:
            err = {"code": "TREE_INVALID", "message": "tree.root_node missing"}
            files_out.append(
                {
                    "pid": pid,
                    "path": rel,
                    "package": "",
                    "imports": [],
                    "symbols": 0,
                    "truncated": False,
                    "error": err,
                }
            )
            errors.append(err)
            continue

        pkg, imports = _parse_package_and_imports(root_node, source)

        before = len(symbols_out)
        file_syms: List[Dict[str, Any]] = []
        _walk(
            root_node,
            source,
            pid=pid,
            cfg=cfg,
            ctx=_Ctx(package=pkg, stack=tuple(), enum_container=None),
            out=file_syms,
            symbols_cap=max_symbols_per_file,
        )

        # Append to global list
        symbols_out.extend(file_syms)
        added = len(symbols_out) - before
        truncated = len(file_syms) >= max_symbols_per_file

        files_out.append(
            {
                "pid": pid,
                "path": rel,
                "package": pkg,
                "imports": imports,
                "symbols": added,
                "truncated": bool(truncated),
                "error": None,
            }
        )

    # Deterministic ordering for symbols: pid, ln, qname, kind, name
    symbols_out = sorted(
        symbols_out,
        key=lambda s: (
            int(s.get("pid", -1)),
            int(s.get("ln", -1)),
            str(s.get("qname", "")),
            str(s.get("kind", "")),
            str(s.get("name", "")),
        ),
    )

    # Deterministic ordering for files: pid then path
    files_out = sorted(files_out, key=lambda f: (int(f.get("pid", -1)), str(f.get("path", ""))))

    # De-dup errors deterministically
    err_key = lambda e: (str(e.get("code", "")), str(e.get("message", "")), str(e.get("detail", "")))
    errors_unique = sorted({err_key(e): e for e in errors}.values(), key=err_key)

    return {
        "version": "3.0",
        "file_count": len(files_out),
        "symbols_count": len(symbols_out),
        "files": files_out,
        "symbols": symbols_out,
        "errors": errors_unique,
    }
