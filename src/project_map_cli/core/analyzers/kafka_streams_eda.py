# utils/digest_tool_v3/analyzers/kafka_streams_eda.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..common import tree_sitter_util as tsu
from .symbol_registry import SymbolRegistry


# --------------------------------------------
# Cheap heuristics (fast + good enough)
# --------------------------------------------

# Detect Kafka Streams “hot” Kotlin files (used in light profile + to avoid noise)
_RE_KAFKA_IMPORT = re.compile(r"org\.apache\.kafka\.streams", re.MULTILINE)
_RE_STREAM_LIKE = re.compile(r"\.(?:stream|table|to|through)\s*\(", re.MULTILINE)

# const val TOPIC = "wde.bars.raw.v5"
_RE_CONST_TOPIC = re.compile(
    r"""\bconst\s+val\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<lit>"[^"\n]*"|\"\"\".*?\"\"\")""",
    re.DOTALL,
)

# Match Kotlin string literal value (supports "..." and """...""")
_RE_STR_LIT = re.compile(r"""(?P<lit>"[^"\n]*"|\"\"\".*?\"\"\")""", re.DOTALL)

# Operation calls of interest
# Captures ".stream( ARG" or ".to(ARG" etc; ARG captured as a shallow expression fragment up to ',' or ')'
_RE_OP_CALL = re.compile(
    r"""\.(?P<op>stream|table|to|through)\s*\(\s*(?P<arg>[^,\)\n]+)""",
    re.MULTILINE,
)

# Serde patterns
_RE_CONSUMED_WITH = re.compile(r"""\bConsumed\s*\.\s*with\s*\(""")
_RE_PRODUCED_WITH = re.compile(r"""\bProduced\s*\.\s*with\s*\(""")
_RE_SERDES = re.compile(r"""\bSerdes\s*\.\s*[A-Za-z_][A-Za-z0-9_]*\b""")
_RE_CUSTOM_SERDE = re.compile(r"""\b[A-Za-z_][A-Za-z0-9_]*Serde\b""")

# Processor API forward routing heuristics
_RE_FORWARD_CALL = re.compile(r"""\b(?P<recv>[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*forward\s*\(""", re.MULTILINE)
_RE_ROUTEDVALUE_FIRST_ARG = re.compile(r"""\bRoutedValue\s*\(\s*(?P<arg1>[^,\)\n]+)""", re.MULTILINE)
_RE_ROUTE_DOT = re.compile(r"""\bRoute\.[A-Za-z_][A-Za-z0-9_]*\b""")
_RE_RECORD_METADATA = re.compile(r"""\brecordMetadata\s*\(\s*\)""", re.MULTILINE)
_RE_TOPIC_CALL = re.compile(r"""\btopic\s*\(\s*\)""", re.MULTILINE)

_FROM_REF_RECORD_METADATA_TOPIC = "<recordMetadata.topic>"


# --------------------------------------------
# Small utilities
# --------------------------------------------

def _rel(cfg: Config, p: Path) -> str:
    try:
        return p.relative_to(cfg.root).as_posix()
    except Exception:
        return p.as_posix()


def _is_hot(text: str) -> bool:
    if not text:
        return False
    return bool(_RE_KAFKA_IMPORT.search(text) or _RE_STREAM_LIKE.search(text))


def _line_of(text: str, idx: int) -> int:
    if idx <= 0:
        return 1
    return text.count("\n", 0, idx) + 1


def _clean_string_lit(lit: str) -> str:
    s = (lit or "").strip()
    if s.startswith('"""') and s.endswith('"""') and len(s) >= 6:
        return s[3:-3]
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return s[1:-1]
    return s


def _parse_const_topics(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _RE_CONST_TOPIC.finditer(text or ""):
        name = (m.group("name") or "").strip()
        lit = (m.group("lit") or "").strip()
        if not name or not lit:
            continue
        if name not in out:
            out[name] = _clean_string_lit(lit)
    return out


def _arg_to_topic(arg_expr: str, const_topics: Dict[str, str]) -> Tuple[Optional[str], Optional[str]]:
    s = (arg_expr or "").strip()
    sm = _RE_STR_LIT.match(s)
    if sm:
        return _clean_string_lit(sm.group("lit")), None
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", s):
        return const_topics.get(s), s
    return None, s


def _window(text: str, start: int, max_chars: int = 6000) -> str:
    end = min(len(text), start + max_chars)
    chunk = text[start:end]
    for pat in (";", "\n\n", "\n}"):
        j = chunk.find(pat)
        if j != -1 and j > 0:
            chunk = chunk[:j]
            break
    return chunk


def _extract_route_ref_from_forward_snippet(snippet: str) -> Tuple[Optional[str], Optional[str]]:
    if not snippet:
        return None, None
    m = _RE_ROUTEDVALUE_FIRST_ARG.search(snippet)
    if not m:
        return None, None
    arg1 = (m.group("arg1") or "").strip()
    if not arg1:
        return None, None
    arg1 = re.sub(r"\s+", " ", arg1)
    rm = _RE_ROUTE_DOT.search(arg1)
    if rm:
        return rm.group(0), None
    return None, arg1


def _find_containing_function(registry: SymbolRegistry, rel_path: str, ln: int) -> Optional[str]:
    # Find the function in this file that contains this line
    best_fqn = None
    best_ln = -1
    for fqn, info in registry.symbols.items():
        if info.file_path == rel_path and info.kind == "function":
            if info.ln <= ln and info.ln > best_ln:
                best_fqn = fqn
                best_ln = info.ln
    return best_fqn


def analyze(
    cfg: Config,
    kt_files: List[Path],
    config_files: List[Path],
    pid_by_path: Any,
    registry: SymbolRegistry,
) -> Dict[str, Any]:
    max_kotlin_files = int(getattr(cfg, "max_kotlin_files", 250) or 250)
    max_topics = int(getattr(cfg, "max_topics", 500) or 500)
    max_edges = int(getattr(cfg, "max_edges", 2000) or 2000)
    max_forward_calls_per_file = int(getattr(cfg, "max_forward_calls_per_file", 200) or 200)
    max_forward_occurrences_total = int(getattr(cfg, "max_forward_occurrences", 2000) or 2000)

    errors: List[Dict[str, Any]] = []
    pid_map = {}
    if isinstance(pid_by_path, dict):
        pid_map = pid_by_path
    
    lang = None
    langr = tsu.load_kotlin_language()
    if langr.ok:
        lang = langr.value

    ordered_files = sorted(kt_files, key=lambda p: _rel(cfg, p))
    topics: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    forward_occurrences: List[Dict[str, Any]] = []

    parsed_files = 0
    for path in ordered_files:
        if parsed_files >= max_kotlin_files:
            break
        rel = _rel(cfg, path)
        pid = -1
        for p, i in pid_map.items():
            if str(p).endswith(rel):
                pid = i
                break

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            continue

        if getattr(cfg, "profile", "full") == "light" and not _is_hot(text):
            continue
        parsed_files += 1

        const_topics = _parse_const_topics(text)
        for name, topic_val in sorted(const_topics.items()):
            if len(topics) >= max_topics: break
            topics.append({"topic": topic_val, "topic_ref": name, "op": "const", "pid": pid, "ln": 1, "file": rel})

        # DSL
        for m in _RE_OP_CALL.finditer(text):
            op = m.group("op")
            arg = m.group("arg")
            if not op or not arg or op not in ("stream", "table"): continue
            
            ln = _line_of(text, m.start())
            src_topic, src_ref = _arg_to_topic(arg, const_topics)
            func = _find_containing_function(registry, rel, ln)
            
            if len(topics) < max_topics:
                topics.append({"topic": src_topic, "topic_ref": src_ref, "op": op, "pid": pid, "ln": ln, "file": rel})

            chunk = _window(text, m.start())
            for sm in _RE_OP_CALL.finditer(chunk):
                sop = sm.group("op")
                if sop not in ("to", "through"): continue
                dst_topic, dst_ref = _arg_to_topic(sm.group("arg"), const_topics)
                dst_ln = _line_of(chunk, sm.start()) + (ln - 1)
                
                edges.append({
                    "from": src_topic, "from_ref": src_ref, "from_fqn": func, "from_type": "code" if func else "topic",
                    "to": dst_topic, "to_ref": dst_ref, "edge_kind": "dsl", "pid": pid, "ln": dst_ln, "file": rel
                })

        # Forward
        for m in _RE_FORWARD_CALL.finditer(text):
            ln = _line_of(text, m.start())
            func = _find_containing_function(registry, rel, ln)
            route_ref, route_unresolved = _extract_route_ref_from_forward_snippet(_window(text, m.start(), 2000))
            
            if route_ref:
                edges.append({
                    "from_fqn": func, "from_type": "code", "to": None, "to_ref": route_ref, 
                    "edge_kind": "forward_route", "pid": pid, "ln": ln, "file": rel
                })

    return {
        "version": "5.0",
        "topics": topics,
        "edges": edges,
        "file_count": parsed_files
    }
