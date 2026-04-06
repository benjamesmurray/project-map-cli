# utils/digest_tool_v3/analyzers/gradle_modules.py
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config


# -----------------------------
# Regexes (cheap, best-effort)
# -----------------------------

# include(":a", ":b")  OR  include(":a")  OR include("a", "b")  OR include 'a'
_RE_INCLUDE_CALL = re.compile(r"\binclude\s*\((?P<args>[^)]*)\)", re.MULTILINE)
_RE_INCLUDE_BARE = re.compile(r"\binclude\s+(?P<args>.+)$", re.MULTILINE)

_RE_QUOTED = re.compile(r"""["']([^"']+)["']""")

# project(":a").projectDir = file("some/path")
_RE_PROJECTDIR = re.compile(
    r"""project\s*\(\s*["'](?P<name>[^"']+)["']\s*\)\s*\.\s*projectDir\s*=\s*file\s*\(\s*["'](?P<dir>[^"']+)["']\s*\)""",
    re.MULTILINE,
)

# plugins:
#   id("foo.bar") version "x"
#   kotlin("jvm")
_RE_PLUGIN_ID = re.compile(r"""id\s*\(\s*["'](?P<id>[^"']+)["']\s*\)""")
_RE_PLUGIN_KOTLIN = re.compile(r"""\bkotlin\s*\(\s*["'](?P<id>[^"']+)["']\s*\)""")

# dependencies:
#   implementation("group:artifact:ver") / api(...) / testImplementation(...) / runtimeOnly(...)
_RE_DEP_COORD = re.compile(
    r"""\b(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|kapt|ksp)\s*\(\s*["'](?P<coord>[^"']+)["']\s*\)""",
    re.MULTILINE,
)

# libs catalog ref:
#   implementation(libs.kafka.streams)
_RE_LIBS_REF = re.compile(r"""\blibs(?:\.[A-Za-z0-9_]+)+\b""")


# -----------------------------
# TOML (optional)
# -----------------------------

def _load_toml(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    """
    Best-effort parse libs.versions.toml. Never raises.
    Returns (doc, error_str).
    """
    try:
        import tomllib  # py311+
        raw = path.read_bytes()
        return tomllib.loads(raw.decode("utf-8", errors="ignore")), None
    except Exception:
        # Try tomli if installed
        try:
            import tomli  # type: ignore
            raw = path.read_bytes()
            return tomli.loads(raw.decode("utf-8", errors="ignore")), None
        except Exception as exc:
            return None, f"toml_parse_failed:{path.as_posix()}:{exc}"


def _catalog_alias_candidates(libs_ref: str) -> List[str]:
    """
    Convert 'libs.foo.bar' into possible catalog aliases.
    Gradle convention often maps dots to '-' or '.'. We try a few.
    """
    parts = libs_ref.split(".")[1:]  # drop 'libs'
    if not parts:
        return []
    dot = ".".join(parts)
    hy = "-".join(parts)
    us = "_".join(parts)
    return [dot, hy, us]


def _resolve_catalog_dep(catalog: dict, libs_ref: str) -> Optional[str]:
    """
    Attempt to resolve libs.<alias> to a concrete 'group:artifact' or 'group:artifact:version' string.
    Best-effort and tolerant.
    """
    if not catalog:
        return None

    libs_tbl = catalog.get("libraries") or {}
    if not isinstance(libs_tbl, dict):
        return None

    for cand in _catalog_alias_candidates(libs_ref):
        v = libs_tbl.get(cand)
        if v is None:
            continue
        # v may be a string "group:artifact:ver" or a table { module = "...", version = "..." }.
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            module = v.get("module") or v.get("group")  # some catalogs use module
            ver = v.get("version")
            if isinstance(module, str) and module:
                if isinstance(ver, str) and ver:
                    # if module is already 'g:a', append ':ver'
                    if module.count(":") == 1:
                        return f"{module}:{ver}"
                    return module
                return module
    return None


# -----------------------------
# Settings parsing
# -----------------------------

def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _pick_root_settings(gradle_files: List[Path], root: Path) -> Optional[Path]:
    """
    Choose the "main" settings file deterministically:
      - prefer shortest repo-relative path
      - then lexicographic
    """
    candidates = [
        p for p in gradle_files
        if p.name in ("settings.gradle", "settings.gradle.kts")
    ]
    if not candidates:
        return None

    def key(p: Path) -> Tuple[int, str]:
        rel = p.relative_to(root).as_posix()
        return (rel.count("/"), rel)

    return sorted(candidates, key=key)[0]


def _parse_includes(settings_text: str) -> List[str]:
    """
    Return Gradle project names as ':a', ':a:b', ...
    Supports:
      include(":a", ":b")
      include("a", "b")
      include ':a', ':b'
      include 'a', 'b'
    """
    out: List[str] = []

    def add_name(raw: str) -> None:
        s = raw.strip()
        if not s:
            return
        # Normalize to ':x' form
        if not s.startswith(":"):
            s = ":" + s
        out.append(s)

    # include(...)
    for m in _RE_INCLUDE_CALL.finditer(settings_text):
        args = m.group("args") or ""
        for qm in _RE_QUOTED.finditer(args):
            add_name(qm.group(1))

    # include 'a', 'b'
    for m in _RE_INCLUDE_BARE.finditer(settings_text):
        args = m.group("args") or ""
        # stop at comment marker (best-effort)
        args = args.split("#", 1)[0].split("//", 1)[0]
        for qm in _RE_QUOTED.finditer(args):
            add_name(qm.group(1))

    # De-dup deterministically
    return sorted(set(out))


def _parse_projectdir_overrides(settings_text: str) -> Dict[str, str]:
    """
    Return { ':a' -> 'sub/dir' } for projectDir overrides where file("...") is used.
    """
    out: Dict[str, str] = {}
    for m in _RE_PROJECTDIR.finditer(settings_text):
        name = (m.group("name") or "").strip()
        d = (m.group("dir") or "").strip()
        if not name:
            continue
        if not name.startswith(":"):
            name = ":" + name
        if d:
            out[name] = d
    return out


def _default_module_dir(name: str) -> str:
    """
    ':a:b' -> 'a/b'
    ':a' -> 'a'
    ':' -> '.'
    """
    if name == ":":
        return "."
    s = name[1:] if name.startswith(":") else name
    return s.replace(":", "/") if s else "."


# -----------------------------
# Build file parsing
# -----------------------------

def _find_build_files(root: Path, module_rel_dir: str) -> List[Path]:
    """
    Return existing build.gradle(.kts) files for a module directory, deterministically.
    """
    mdir = (root / module_rel_dir).resolve()
    candidates = [
        mdir / "build.gradle",
        mdir / "build.gradle.kts",
    ]
    return [p for p in candidates if p.exists() and p.is_file()]


def _parse_build_file(text: str, catalog: Optional[dict]) -> Dict[str, Any]:
    """
    Cheap parse for plugins + deps + libs refs.
    """
    plugins: List[str] = []
    for m in _RE_PLUGIN_ID.finditer(text):
        plugins.append(m.group("id"))
    for m in _RE_PLUGIN_KOTLIN.finditer(text):
        # kotlin("jvm") -> org.jetbrains.kotlin.jvm (rough)
        plugins.append(f"org.jetbrains.kotlin.{m.group('id')}")

    deps: List[str] = []
    for m in _RE_DEP_COORD.finditer(text):
        deps.append(m.group("coord"))

    libs_refs = sorted(set(_RE_LIBS_REF.findall(text)))

    # Resolve libs refs to coords (best-effort)
    resolved: List[str] = []
    if catalog:
        for ref in libs_refs:
            r = _resolve_catalog_dep(catalog, ref)
            if r:
                resolved.append(r)
    resolved = sorted(set(resolved))

    return {
        "plugins": sorted(set(plugins)),
        "deps": sorted(set(deps)),
        "libs_refs": libs_refs,          # already sorted unique
        "libs_resolved": resolved,       # sorted unique
    }


def _signal_kafka(dep_like: str) -> bool:
    s = dep_like.lower()
    # primary:
    if "kafka-streams" in s:
        return True
    if "org.apache.kafka" in s:
        return True
    # fallback heuristic
    return ("kafka" in s and "streams" in s)


def _signal_kotlinx_serialization(dep_like: str) -> bool:
    s = dep_like.lower()
    if "org.jetbrains.kotlin.plugin.serialization" in s:
        return True
    if "kotlinx-serialization" in s:
        return True
    if "org.jetbrains.kotlinx:serialization" in s:
        return True
    return False


def _cap_list(xs: List[str], max_n: int) -> List[str]:
    if max_n <= 0:
        return []
    if len(xs) <= max_n:
        return xs
    return xs[:max_n]


# -----------------------------
# Public analyzer
# -----------------------------

@dataclass(frozen=True)
class _ModuleRec:
    name: str            # ':engine'
    path: str            # 'engine' or overridden
    build_files: Tuple[str, ...]  # repo-relative posix strings


def analyze(cfg: Config, gradle_files: List[Path], pid_by_path: Dict[Path, int]) -> Dict[str, Any]:
    """
    Infer Gradle module structure + coarse dependency signals without executing Gradle.

    Output shape is intentionally small and deterministic:

    {
      "version": "3.0",
      "settings": {"file": "settings.gradle.kts", "pid": 12, "modules_count": 3} | null,
      "catalog": {"file": ".../libs.versions.toml", "pid": 34, "parsed": true, "libraries_count": 10, "plugins_count": 2} | null,
      "modules": [
        {
          "name": ":engine",
          "path": "engine",
          "kafka": true,
          "kotlinx_serialization": true,
          "build_files": [{"rel": "engine/build.gradle.kts", "pid": 56}],
          "plugins": [... capped ...],
          "deps_sample": [... capped ...],
          "libs_refs_sample": [... capped ...],
          "libs_resolved_sample": [... capped ...]
        }
      ],
      "errors": [...]
    }
    """
    root = cfg.root
    errors: List[str] = []

    # Pick settings file
    settings_path = _pick_root_settings(gradle_files, root)
    settings_doc: Optional[dict] = None
    includes: List[str] = []
    overrides: Dict[str, str] = {}

    if settings_path:
        stxt = _read_text(settings_path)
        includes = _parse_includes(stxt)
        overrides = _parse_projectdir_overrides(stxt)
        settings_doc = {
            "file": settings_path.relative_to(root).as_posix(),
            "pid": int(pid_by_path.get(settings_path, -1)),
            "modules_count": len(includes),
        }

    # libs.versions.toml (choose closest-to-root deterministically)
    catalog_path: Optional[Path] = None
    catalog_candidates = [p for p in gradle_files if p.name == "libs.versions.toml"]
    if catalog_candidates:
        catalog_path = sorted(
            catalog_candidates,
            key=lambda p: (p.relative_to(root).as_posix().count("/"), p.relative_to(root).as_posix()),
        )[0]

    catalog_doc: Optional[dict] = None
    catalog: Optional[dict] = None
    if catalog_path:
        catalog, err = _load_toml(catalog_path)
        if err:
            errors.append(err)
        libs_tbl = (catalog or {}).get("libraries") if isinstance(catalog, dict) else None
        plugins_tbl = (catalog or {}).get("plugins") if isinstance(catalog, dict) else None
        catalog_doc = {
            "file": catalog_path.relative_to(root).as_posix(),
            "pid": int(pid_by_path.get(catalog_path, -1)),
            "parsed": bool(catalog is not None),
            "libraries_count": int(len(libs_tbl)) if isinstance(libs_tbl, dict) else 0,
            "plugins_count": int(len(plugins_tbl)) if isinstance(plugins_tbl, dict) else 0,
        }

    # Determine module list
    module_names: List[str]
    if includes:
        module_names = includes
    else:
        # Single-module fallback: if root has build.gradle(.kts), treat as ':'
        root_build = [root / "build.gradle", root / "build.gradle.kts"]
        if any(p.exists() for p in root_build):
            module_names = [":"]
        else:
            module_names = []  # no gradle signal at all

    # Always consider root module ":" if root build exists (even in multi-module)
    root_build_exists = (root / "build.gradle").exists() or (root / "build.gradle.kts").exists()
    if root_build_exists and ":" not in module_names:
        module_names = [":"] + module_names

    # Construct module records deterministically
    module_recs: List[_ModuleRec] = []
    for name in sorted(set(module_names)):
        mdir = overrides.get(name) or _default_module_dir(name)
        build_files = _find_build_files(root, mdir)
        build_rels = tuple(sorted((p.relative_to(root).as_posix() for p in build_files)))
        module_recs.append(_ModuleRec(name=name, path=mdir, build_files=build_rels))

    # Parse build files for coarse signals
    modules_out: List[Dict[str, Any]] = []
    for mr in sorted(module_recs, key=lambda x: x.name):
        all_plugins: List[str] = []
        all_deps: List[str] = []
        all_lib_refs: List[str] = []
        all_lib_resolved: List[str] = []

        kafka = False
        kotlinx_ser = False

        build_files_out: List[Dict[str, Any]] = []

        for rel in mr.build_files:
            p = root / rel
            pid = int(pid_by_path.get(p, -1))
            build_files_out.append({"rel": rel, "pid": pid})

            txt = _read_text(p)
            parsed = _parse_build_file(txt, catalog)

            all_plugins.extend(parsed["plugins"])
            all_deps.extend(parsed["deps"])
            all_lib_refs.extend(parsed["libs_refs"])
            all_lib_resolved.extend(parsed["libs_resolved"])

            for x in parsed["plugins"] + parsed["deps"] + parsed["libs_resolved"]:
                if _signal_kafka(x):
                    kafka = True
                if _signal_kotlinx_serialization(x):
                    kotlinx_ser = True

        # Deterministic unique sets
        plugins = sorted(set(all_plugins))
        deps = sorted(set(all_deps))
        libs_refs = sorted(set(all_lib_refs))
        libs_resolved = sorted(set(all_lib_resolved))

        # Keep the shard small; these are hints not a full lockfile.
        plugins = _cap_list(plugins, 30)
        deps_sample = _cap_list(deps, 40)
        libs_refs_sample = _cap_list(libs_refs, 40)
        libs_resolved_sample = _cap_list(libs_resolved, 40)

        modules_out.append(
            {
                "name": mr.name,
                "path": mr.path,
                "kafka": bool(kafka),
                "kotlinx_serialization": bool(kotlinx_ser),
                "build_files": sorted(build_files_out, key=lambda r: (r.get("rel", ""), int(r.get("pid", -1)))),
                "plugins": plugins,
                "deps_sample": deps_sample,
                "libs_refs_sample": libs_refs_sample,
                "libs_resolved_sample": libs_resolved_sample,
            }
        )

    # Final deterministic sort
    modules_out = sorted(modules_out, key=lambda m: str(m.get("name", "")))

    return {
        "version": "3.0",
        "settings": settings_doc,
        "catalog": catalog_doc,
        "modules": modules_out,
        "errors": sorted(set(errors)),
    }
