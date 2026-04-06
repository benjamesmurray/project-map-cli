# utils/digest_tool_v3/common/filters.py
from __future__ import annotations

import sys
import re
from functools import lru_cache
from typing import Iterable, Pattern


# ---- stdlib detection --------------------------------------------------------

@lru_cache(maxsize=1)
def _stdlib_names() -> set[str]:
    """
    Best-effort stdlib module name set.

    Prefers sys.stdlib_module_names (3.10+). Falls back to a conservative
    prefix list for common stdlib packages.
    """
    names: set[str] = set()
    if hasattr(sys, "stdlib_module_names"):
        try:
            # type: ignore[attr-defined]
            names = set(sys.stdlib_module_names)  # py310+
        except Exception:
            names = set()
    if not names:
        # Fallback heuristic: treat these as stdlib top-level packages
        names.update({
            "abc", "argparse", "asyncio", "base64", "bisect", "collections",
            "concurrent", "contextlib", "copy", "csv", "dataclasses", "datetime",
            "decimal", "enum", "functools", "glob", "gzip", "hashlib", "heapq",
            "http", "importlib", "inspect", "io", "itertools", "json", "logging",
            "math", "mimetypes", "numbers", "operator", "os", "pathlib", "pickle",
            "platform", "plistlib", "random", "re", "sched", "secrets", "shlex",
            "shutil", "socket", "sqlite3", "ssl", "statistics", "string",
            "subprocess", "sys", "tempfile", "textwrap", "threading", "time",
            "types", "typing", "uuid", "urllib", "venv", "warnings", "weakref",
            "xml", "zipfile", "zoneinfo",
        })
    return names


def _top(modname: str) -> str:
    """Return the top-level package segment of a dotted module name."""
    return modname.split(".", 1)[0] if modname else ""


def is_stdlib_module(modname: str) -> bool:
    """
    True if the module appears to be from the Python standard library.

    Uses sys.stdlib_module_names when available; else top-level prefix heuristic.
    """
    if not modname:
        return False
    top = _top(modname)
    return top in _stdlib_names()


# ---- repo / namespace filtering ---------------------------------------------

def is_repo_module(modname: str, ns_allow_re: Pattern[str]) -> bool:
    """
    True if 'modname' matches the configured repository namespace allow-list.
    """
    if not modname:
        return False
    return bool(ns_allow_re.search(modname))


def should_include_import_edge(src_mod: str, dst_mod: str, ns_allow_re: Pattern[str]) -> bool:
    """
    Keep only repo↔repo edges.
    """
    return is_repo_module(src_mod, ns_allow_re) and is_repo_module(dst_mod, ns_allow_re)


def should_keep_ctor_symbol(qualname: str, ns_allow_re: Pattern[str]) -> bool:
    """
    True if a fully-qualified class name is in-repo (per allow-list) and not stdlib.
    """
    if not qualname:
        return False
    if not is_repo_module(qualname, ns_allow_re):
        return False
    # If someone names their package same as stdlib top, allow-list should already gate it.
    return True


# ---- attribute / builtin banlist --------------------------------------------

# Common attribute names that are *not* constructor symbols and should be dropped
# from call-site analysis when they appear as Attribute(...).func identifiers.
_ATTR_BANLIST: set[str] = {
    # dict/list methods
    "get", "keys", "items", "values", "update", "pop", "append", "extend", "insert",
    "remove", "clear", "add", "discard",
    # string-ish
    "format", "lower", "upper", "strip", "split", "join", "replace",
    # numbers/math-like instance methods (rare in practice)
    "bit_length",
    # misc
    "copy",
}

# Known top-level builtins / calls that we never want to treat as ctor-like
_BUILTIN_FUNC_NAMES: set[str] = {
    "len", "range", "print", "sorted", "sum", "min", "max", "any", "all",
    "map", "filter", "zip", "enumerate", "reversed", "next", "iter", "list",
    "dict", "set", "tuple", "bool", "int", "float", "str", "bytes", "bytearray",
    "object", "type", "super",
}


def omit_attr_name(name: str) -> bool:
    """
    True if an attribute name should be dropped from ctor consideration.
    """
    return name in _ATTR_BANLIST


def is_builtin_call(name: str) -> bool:
    """
    True if a bare Name(...) call is clearly a builtin/common function call.
    """
    return name in _BUILTIN_FUNC_NAMES


# ---- build / generated artefact filtering -----------------------------------
#
# The repo scanner (fs_scan.py) primarily controls exclusion via config globs.
# These helpers are for analyzers to avoid spending time indexing generated
# sources even if they slip through (or if a user overrides excludes).
#
# Paths are assumed to be repo-relative POSIX strings.
#

# Gradle/Kotlin/JVM build outputs and caches
_BUILD_ARTEFACT_RE: Pattern[str] = re.compile(
    r"(?i)"
    r"(^|/)"
    r"(\.gradle|\.kotlin|build|out|target)"
    r"(/|$)"
)

# Common generated-source directories (include JVM + general tooling)
_GENERATED_RE: Pattern[str] = re.compile(
    r"(?i)"
    r"(^|/)"
    r"("
    r"build/generated"
    r"|build/tmp"
    r"|build/resources"
    r"|build/intermediates"
    r"|generated(-sources)?"
    r"|generated_src"
    r"|src/generated"
    r"|src/gen"
    r"|gen"
    r"|autogen"
    r"|\.idea|\.vscode"  # editor outputs sometimes materialize generated stubs
    r")"
    r"(/|$)"
)

# Kotlin/Gradle-specific common patterns that might be worth excluding later.
# (Not used directly by fs_scan; these are analyzer guardrails.)
_GRADLE_WRAPPER_RE: Pattern[str] = re.compile(r"(^|/)(gradle/wrapper)(/|$)", re.IGNORECASE)


def is_build_artefact_path(rel_posix: str) -> bool:
    """
    True if the repo-relative path points into a build output/cache directory
    commonly produced by Gradle/Kotlin/JVM builds.
    """
    if not rel_posix:
        return False
    rel_posix = rel_posix.lstrip("./")
    return bool(_BUILD_ARTEFACT_RE.search(rel_posix))


def is_generated_source_path(rel_posix: str) -> bool:
    """
    True if the path looks like generated sources/resources.
    """
    if not rel_posix:
        return False
    rel_posix = rel_posix.lstrip("./")
    return bool(_GENERATED_RE.search(rel_posix))


def is_gradle_wrapper_path(rel_posix: str) -> bool:
    """
    True if the path is Gradle wrapper tooling. (Usually not useful for LLM digests.)
    """
    if not rel_posix:
        return False
    rel_posix = rel_posix.lstrip("./")
    return bool(_GRADLE_WRAPPER_RE.search(rel_posix))


def should_skip_code_indexing(rel_posix: str) -> bool:
    """
    Conservative "do not waste time" gate for analyzers.
    - Build artefacts: skip
    - Generated code: skip
    Wrapper files are NOT skipped here (some teams want versions), but callers can.
    """
    return is_build_artefact_path(rel_posix) or is_generated_source_path(rel_posix)


# ---- utilities ---------------------------------------------------------------

def normalize_module_name(candidate: str) -> str:
    """
    Normalize a possibly file-ish or dotted 'candidate' into a dotted module-ish name.
    This is a best-effort helper; analyzers should prefer their own resolution rules.

    Examples:
      'well_detection_engine/engine/runtime_sweep.py' -> 'well_detection_engine.engine.runtime_sweep'
      'well_detection_engine.engine.runtime_sweep'    -> unchanged
    """
    if not candidate:
        return candidate
    if candidate.endswith(".py"):
        candidate = candidate[:-3]
    return candidate.replace("/", ".").replace("\\", ".")
