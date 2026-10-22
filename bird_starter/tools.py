"""Read-only SQLite tools; suitable as both Python tools and a Mock MCP backend."""
from __future__ import annotations
import sqlite3
import time
import math
from contextlib import closing
from pathlib import Path
import sqlglot
from sqlglot import exp


_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect)
_DANGEROUS_FUNCTIONS = {"load_extension", "readfile", "writefile", "eval", "fts3_tokenizer"}


class SQLSafetyError(ValueError):
    pass


def _dangerous_function(name: str) -> bool:
    return name.lower() in _DANGEROUS_FUNCTIONS or name.lower().startswith("pragma_")


def _query_authorizer(action, arg1, arg2, database, source):
    if action == sqlite3.SQLITE_FUNCTION and _dangerous_function(arg2 or ""):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and database not in {None, "main"}:
        return sqlite3.SQLITE_DENY
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def check_read_only_sql(sql: str) -> str:
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 100000:
        raise SQLSafetyError("Expected non-empty SQL of at most 100000 characters")
    sql = sql.strip().removesuffix(";").strip()
    statements = sqlglot.parse(sql, read="sqlite")
    if len(statements) != 1 or not isinstance(statements[0], _ALLOWED_ROOTS):
        raise SQLSafetyError("Only one SELECT/CTE/UNION-style statement is permitted")
    for forbidden in (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Into):
        if statements[0].find(forbidden) is not None:
            raise SQLSafetyError("SQL contains a forbidden operation")
    for node in statements[0].find_all(exp.Func):
        name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
        if _dangerous_function(name):
            raise SQLSafetyError("SQL contains a forbidden function")
    for table in statements[0].find_all(exp.Table):
        if table.catalog or (table.db and table.db.lower() != "main"):
            raise SQLSafetyError("Queries may only read the configured main database")
    return sql


def ro_connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).resolve(strict=True)
    # mode=ro prevents changes even if a SQL guard is accidentally bypassed.
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def schema_catalog(db_path: str | Path) -> dict[str, str]:
    with closing(ro_connect(db_path)) as conn:
        rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return {name: ddl or name for name, ddl in rows}


def execute_select(db_path: str | Path, sql: str, *, max_rows: int = 10000,
                   timeout_seconds: float = 8.0, max_result_bytes: int = 1000000) -> dict:
    """Return bounded results, never silently treating truncated output as complete."""
    if type(max_rows) is not int or not 1 <= max_rows <= 10000:
        raise ValueError("max_rows must be between 1 and 10000")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if type(max_result_bytes) is not int or max_result_bytes <= 0:
        raise ValueError("max_result_bytes must be positive")
    sql = check_read_only_sql(sql)
    start = time.monotonic()
    conn = ro_connect(db_path)
    try:
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2000000)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 100000)
        conn.set_authorizer(_query_authorizer)
        conn.set_progress_handler(lambda: int(time.monotonic() - start > timeout_seconds), 1000)
        cursor = conn.execute(sql)
        cols = [c[0] for c in cursor.description] if cursor.description else []
        rows, result_bytes, complete = [], 0, True
        for row in cursor:
            if time.monotonic() - start > timeout_seconds:
                raise sqlite3.OperationalError("interrupted")
            size = sum(len(value) if isinstance(value, bytes) else len(value.encode("utf-8"))
                       if isinstance(value, str) else 8 for value in row)
            if len(rows) >= max_rows or result_bytes + size > max_result_bytes:
                complete = False
                break
            rows.append(row)
            result_bytes += size
        return {"columns": cols, "rows": rows, "complete": complete,
                "elapsed_ms": round((time.monotonic() - start) * 1000, 1)}
    finally:
        conn.close()
