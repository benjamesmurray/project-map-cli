# utils/digest_tool_v3/analyzers/db_schema.py
from __future__ import annotations

import os
import sqlite3
import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..common import ast_utils


def _sorted_cols(cols: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(cols, key=lambda c: c["name"])


def _table_record(
    name: str,
    columns: List[Dict[str, Any]],
    pk: List[str],
    fks: List[Dict[str, Any]] | None = None,
    indexes: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "name": name,
        "columns": _sorted_cols(columns),
        "pk": sorted(pk),
    }
    if fks:
        rec["fk"] = sorted(
            [{"from": f["from"], "to": f["to"], "table": f["table"]} for f in fks],
            key=lambda x: (x["table"], x["from"], x["to"]),
        )
    if indexes:
        rec["indexes"] = sorted(
            [{"name": i["name"], "unique": bool(i.get("unique", False))} for i in indexes],
            key=lambda x: (x["name"], int(x["unique"])),
        )
    return rec


# ----------------------------- SQLite introspection -----------------------------

def _sqlite_open_ro(path: Path) -> sqlite3.Connection:
    # URI open in read-only mode
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _sqlite_tables(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
    )
    return [r[0] for r in cur.fetchall()]


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
    cur = conn.execute(f"PRAGMA table_info('{table}')")
    cols: List[Dict[str, Any]] = []
    pk: List[str] = []
    for _, name, typ, notnull, _dflt_value, is_pk in cur.fetchall():
        cols.append({"name": name, "type": (typ or "").strip(), "nullable": (not notnull)})
        if is_pk:
            pk.append(name)
    return cols, pk


def _sqlite_foreign_keys(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    cur = conn.execute(f"PRAGMA foreign_key_list('{table}')")
    out: List[Dict[str, Any]] = []
    # columns: id, seq, table, from, to, on_update, on_delete, match
    for _id, _seq, ref_table, from_col, to_col, *_ in cur.fetchall():
        out.append({"table": ref_table, "from": from_col, "to": to_col})
    return out


def _sqlite_indexes(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    cur = conn.execute(f"PRAGMA index_list('{table}')")
    out: List[Dict[str, Any]] = []
    # columns: seq, name, unique, origin, partial
    for _seq, name, unique, *_ in cur.fetchall():
        out.append({"name": name, "unique": bool(unique)})
    return out


def _reflect_sqlite(db_files: List[Path]) -> Dict[str, Any]:
    tables_all: List[Dict[str, Any]] = []
    reasons: List[str] = []

    for dbf in db_files:
        try:
            with _sqlite_open_ro(dbf) as conn:
                for t in _sqlite_tables(conn):
                    cols, pk = _sqlite_columns(conn, t)
                    fks = _sqlite_foreign_keys(conn, t)
                    idx = _sqlite_indexes(conn, t)
                    tables_all.append(_table_record(t, cols, pk, fks, idx))
        except Exception as exc:
            reasons.append(f"sqlite({dbf.name}): {type(exc).__name__}")

    # Deterministic sort by table name
    tables_all.sort(key=lambda t: t["name"])
    out: Dict[str, Any] = {"tables": tables_all}
    if reasons:
        out["reason"] = "; ".join(sorted(set(reasons)))
    return out


# ----------------------------- SQLAlchemy (static scan, optional runtime) ------

def _scan_sqlalchemy_models(py_files: List[Path], cfg: Config) -> List[Tuple[str, str]]:
    """
    Best-effort static scan: return [(model_name, module)] where a class subclasses something called 'Base'.
    We DO NOT import user code unless explicitly enabled via env.
    This gives a minimal ORM mapping even when imports are disabled.
    """
    hits: List[Tuple[str, str]] = []
    for path in py_files:
        mod = ast_utils.module_name_from_path(path, cfg.root)
        try:
            tree = ast_utils.parse_tree(path)
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Heuristic: subclass 'Base' or endswith '.Base' (typical SQLAlchemy pattern)
                for b in node.bases:
                    bname = ast_utils._attr_to_str(b) or ""
                    if bname.endswith(".Base") or bname == "Base":
                        hits.append((node.name, mod))
                        break
    # Deduplicate deterministically
    seen = set()
    out: List[Tuple[str, str]] = []
    for name, mod in sorted(hits, key=lambda x: (x[1], x[0])):
        key = (name, mod)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _reflect_sqlalchemy_runtime(cfg: Config) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Optional runtime reflection if env DIGEST_V3_ENABLE_SQLALCHEMY_IMPORTS=1.
    We attempt a light import of metadata if a common location can be guessed.
    If not enabled or fails, return ([], reason).
    """
    if os.getenv("DIGEST_V3_ENABLE_SQLALCHEMY_IMPORTS") != "1":
        return [], "sqlalchemy: imports disabled"
    # User-specific: we don't know where the metadata lives; bail out deterministically.
    return [], "sqlalchemy: no runtime hook configured"


# ----------------------------- Postgres (optional) -----------------------------

def _resolve_pg_dsn(cfg: Config) -> Tuple[Optional[str], str]:
    """
    Resolve a Postgres DSN, checking in order:
      1) cfg.db_url_env (from --db-url-env; default 'PG_DSN')
      2) TIMESCALE_DSN
      3) PG_DSN

    Returns (dsn|None, source_tag).
    """
    # 1) primary (user-specified env var name)
    primary = (cfg.db_url_env or "PG_DSN").strip()
    if primary and os.getenv(primary):
        return os.getenv(primary), primary

    # 2) fallback: TIMESCALE_DSN
    if os.getenv("TIMESCALE_DSN"):
        return os.getenv("TIMESCALE_DSN"), "TIMESCALE_DSN"

    # 3) fallback: PG_DSN
    if os.getenv("PG_DSN"):
        return os.getenv("PG_DSN"), "PG_DSN"

    return None, primary or "PG_DSN"


def _reflect_postgres(dsn: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Reflect a Postgres/Timescale schema using psycopg2 if available.
    """
    try:
        import psycopg2  # type: ignore
    except Exception:
        return [], "postgres: psycopg2 not available"

    tables: List[Dict[str, Any]] = []
    reason: Optional[str] = None

    try:
        with psycopg2.connect(dsn) as conn:  # type: ignore
            with conn.cursor() as cur:
                # Tables
                cur.execute("""
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema')
                    ORDER BY table_schema, table_name
                """)
                rows = cur.fetchall()
                for schema, table in rows:
                    # Columns
                    cur.execute("""
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema=%s AND table_name=%s
                        ORDER BY ordinal_position
                    """, (schema, table))
                    cols_rows = cur.fetchall()
                    cols = [{"name": r[0], "type": r[1], "nullable": (r[2] == "YES")} for r in cols_rows]

                    # PK
                    cur.execute("""
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                        WHERE i.indrelid = %s::regclass AND i.indisprimary
                    """, (f"{schema}.{table}",))
                    pk = [r[0] for r in cur.fetchall()]

                    tables.append(_table_record(f"{schema}.{table}", cols, pk))
    except Exception as exc:
        reason = f"postgres: {type(exc).__name__}"

    tables.sort(key=lambda t: t["name"])
    return tables, reason


# ----------------------------- Public API -------------------------------------

import re

_RE_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_.]*)\s*\((?P<body>.*?)\);", re.IGNORECASE | re.DOTALL)
_RE_COLUMN = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<type>[A-Za-z_][A-Za-z0-9_]*(?:\s*\([^)]*\))?)", re.MULTILINE | re.IGNORECASE)

def _parse_sql_files(sql_files: List[Path]) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    for sf in sql_files:
        try:
            text = sf.read_text(encoding="utf-8", errors="ignore")
            for m in _RE_CREATE_TABLE.finditer(text):
                table_name = m.group("name").strip().split(".")[-1]
                body = m.group("body")
                cols = []
                pk = []
                for cm in _RE_COLUMN.finditer(body):
                    cname = cm.group("name")
                    ctype = cm.group("type")
                    if cname.upper() in ("PRIMARY", "FOREIGN", "CONSTRAINT", "CHECK", "UNIQUE"):
                        continue
                    cols.append({"name": cname, "type": ctype, "nullable": True})
                    if "PRIMARY KEY" in body.upper() and cname.upper() in body.upper():
                        # very simple PK detection
                        pass
                tables.append(_table_record(table_name, cols, pk))
        except Exception:
            continue
    return tables

def analyze(
    cfg: Config,
    py_files: List[Path],
    sql_files: List[Path],
    sqlite_files: List[Path],
    pid_by_path: Dict[Path, int],
) -> Dict[str, Any]:
    """
    Emit a single shard:
      {
        "tables": [...],
        "orm_models": [...],
        "reason": "optional; concatenated reasons from skipped backends"
      }
    Preference order (merged): SQLAlchemy (static scan + optional runtime), SQLite, Postgres, SQL Files.
    """
    reasons: List[str] = []

    # ORM models (static-only; gives a mapping signal without imports)
    orm_pairs = _scan_sqlalchemy_models(py_files, cfg)
    orm_models = [{"model": name, "module": mod} for (name, mod) in orm_pairs]

    # Optional runtime SQLAlchemy reflection (disabled by default)
    sa_tables_rt, sa_reason = _reflect_sqlalchemy_runtime(cfg)
    if sa_reason:
        reasons.append(sa_reason)

    # SQLite files
    sqlite_tables: List[Dict[str, Any]] = []
    if sqlite_files:
        sqlite_tables_doc = _reflect_sqlite(sqlite_files)
        sqlite_tables = sqlite_tables_doc.get("tables", [])
        if sqlite_tables_doc.get("reason"):
            reasons.append(str(sqlite_tables_doc["reason"]))

    # Postgres/Timescale (via env var resolution)
    dsn, dsn_source = _resolve_pg_dsn(cfg)
    pg_tables: List[Dict[str, Any]] = []
    if dsn:
        pg_tables, pg_reason = _reflect_postgres(dsn)
        if pg_reason:
            reasons.append(pg_reason)
        else:
            reasons.append(f"postgres: using {dsn_source}")
    else:
        reasons.append(f"postgres: {dsn_source} not set")

    # SQL Files (static parse)
    static_sql_tables = _parse_sql_files(sql_files)

    # Merge tables; often refer to different DBs. Concatenate and de-dup by name.
    tables: List[Dict[str, Any]] = []
    tables.extend(sa_tables_rt)
    tables.extend(sqlite_tables)
    tables.extend(pg_tables)
    tables.extend(static_sql_tables)
    seen = set()
    unique_tables: List[Dict[str, Any]] = []
    for t in sorted(tables, key=lambda t: t["name"]):
        key = t["name"]
        if key in seen:
            continue
        seen.add(key)
        unique_tables.append(t)

    out: Dict[str, Any] = {
        "tables": unique_tables,
        "orm_models": orm_models,
    }
    reasons = [r for r in reasons if r]
    if reasons:
        out["reason"] = "; ".join(sorted(set(reasons)))
    return out
