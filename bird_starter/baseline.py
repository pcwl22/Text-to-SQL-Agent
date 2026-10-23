"""One full-schema candidate, no graph selection or SQL repair."""
import hashlib
import sqlite3
import time

from sqlglot.errors import ParseError
from .data import AgentInput
from .model import ModelFailure, extract_sql, redact
from .observability import observe_schema, model_call_metrics
from .prompts import generation_prompt
from .tools import SQLSafetyError, execute_select, schema_catalog


def error_info(exc: Exception, stage: str) -> tuple[str, str]:
    if isinstance(exc, ModelFailure):
        return exc.category, exc.category
    if stage == "model":
        return "model_error", "Model request failed; provider details omitted"
    if isinstance(exc, SQLSafetyError):
        return "sql_safety_error", str(exc)
    if isinstance(exc, ParseError):
        return "sql_syntax_error", "SQL AST parsing failed"
    if isinstance(exc, sqlite3.Error):
        text = str(exc).lower()
        if "interrupted" in text:
            category = "sql_timeout"
        elif any(part in text for part in ("syntax error", "incomplete input", "unrecognized token")):
            category = "sql_syntax_error"
        elif "not authorized" in text or "prohibited" in text:
            category = "sql_safety_error"
        elif "no such table" in text or "no such column" in text or "ambiguous column" in text:
            category = "sql_schema_error"
        elif "too big" in text:
            category = "sql_resource_error"
        else:
            category = "sql_execution_error"
        return category, redact(str(exc))[:500]
    return ("data_error" if stage == "data" else "internal_error"), redact(str(exc))[:500]


def run_baseline(agent_input: AgentInput, model, *, sql_timeout: float = 8.0,
                 max_rows: int = 1000, max_prompt_chars: int = 200000, dry_run: bool = False) -> dict:
    result = {"generated_sql": "", "generation_success": False, "execution_ok": False,
              "execution_error_type": None, "execution_error_message": None,
              "execution_time_ms": None, "output_truncated": None, "attempts": 0,
              "error_type": None, "error_message": None, "dry_run": dry_run,
              "status": "failed", "node_metrics": {}, "trace": []}
    stage = "data"
    try:
        catalog = schema_catalog(agent_input.db_path)
        if not catalog:
            raise ValueError("Database has no user tables")
        schema = "\n".join(catalog.values())
        result.update(schema_tables=list(catalog), schema_sha256=hashlib.sha256(schema.encode()).hexdigest())
        result["schema_observation"] = observe_schema(schema,
            full_schema_sha256=result["schema_sha256"], fallback_reason="disabled")
        system, user = generation_prompt(agent_input.question, agent_input.evidence, schema)
        if len(system) + len(user) > max_prompt_chars:
            raise ValueError("Full schema prompt exceeds configured character limit; no schema was pruned")
        if dry_run:
            result.update(generation_success=None, execution_ok=None, status="dry_run_ok", preflight_ok=True)
            return result
        stage = "model"
        called = time.monotonic()
        try:
            sql = extract_sql(model.ask(system, user))
        finally:
            result["node_metrics"]["generation"] = model_call_metrics(model, called, time.monotonic())
            result["trace"].append({"node": "generate", "prompt_chars": len(system) + len(user),
                "schema_sha256": result["schema_sha256"], **result["node_metrics"]["generation"]})
        if not sql:
            raise ModelFailure("empty_response")
        result.update(generated_sql=sql, generation_success=True, attempts=1)
        stage = "execute"
        start = time.monotonic()
        try:
            executed = execute_select(agent_input.db_path, sql, max_rows=max_rows, timeout_seconds=sql_timeout)
        finally:
            result["execution_time_ms"] = round((time.monotonic() - start) * 1000, 3)
            result["node_metrics"]["execution"] = {"tool_calls": 1, "latency_ms": result["execution_time_ms"]}
        result.update(execution_ok=True, output_truncated=not executed["complete"], status="execution_succeeded")
    except Exception as exc:
        category, message = error_info(exc, stage)
        result.update(error_type=category, error_message=message)
        if stage == "execute":
            result.update(execution_error_type=category, execution_error_message=message)
    return result
