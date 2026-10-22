"""Local smoke-check proxy. Official BIRD EX must use the benchmark evaluator."""
from __future__ import annotations
from collections import Counter
from contextlib import ExitStack, closing
import time
from .tools import check_read_only_sql, execute_select, ro_connect


def schema_compatibility_report(cases: list[dict], db_paths: dict, timeout_seconds: float = 8.0) -> dict:
    """Offline evaluator-only compilation of gold SQL; no answer rows or SQL in reports.

    EXPLAIN QUERY PLAN checks SQLite syntax, table/column/function resolution. It
    does not execute the gold query and is neither semantic validation nor EX.
    """
    errors, connections = [], {}
    with ExitStack() as stack:
        for case in cases:
            try:
                db_id = case["db_id"]
                if db_id not in connections:
                    connections[db_id] = stack.enter_context(closing(ro_connect(db_paths[db_id])))
                conn = connections[db_id]
                start = time.monotonic()
                conn.set_progress_handler(lambda: int(time.monotonic() - start > timeout_seconds), 1000)
                sql = check_read_only_sql(case["SQL"])
                conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
            except Exception as exc:
                # SQL parser/SQLite messages may echo gold text: only report class + IDs.
                errors.append({"case_id": case["case_id"], "db_id": case["db_id"],
                               "error_type": type(exc).__name__})
    return {"checked": len(cases), "passed": len(cases) - len(errors),
            "failed": len(errors), "errors": errors}


def result_match_proxy(db_path: str, predicted_sql: str, gold_sql: str) -> bool | None:
    """Order-insensitive row comparison; not official BIRD EX (column permutations differ)."""
    try:
        p = execute_select(db_path, predicted_sql, max_rows=10000)
        g = execute_select(db_path, gold_sql, max_rows=10000)
        if not (p["complete"] and g["complete"]):
            return None
        # Coerce numeric results only where SQLite itself says they compare numerically.
        return Counter(tuple(row) for row in p["rows"]) == Counter(tuple(row) for row in g["rows"])
    except Exception:
        return False
