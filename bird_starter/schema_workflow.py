"""Shared schema graph with optional, bounded deterministic-error SQL repair."""
import time
import math
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from .baseline import error_info
from .model import ModelFailure, extract_sql, redact
from .observability import observe_schema, model_call_metrics
from .prompts import generation_prompt
from .schema_linking import (SelectionError, read_schema, linking_prompt, validate_selection,
                             full_selection, render_selection, schema_metrics)
from .schema_routing import AdaptiveSchemaGate, route_schema
from .tools import execute_select
from .sql_repair import (ErrorCategory, REPAIRABLE, classify_error, failure_fields,
                         validate_candidate, repair_prompt)


class GraphInput(TypedDict):
    question: str
    evidence: str
    db_path: str
    mode: str


class SchemaState(GraphInput, total=False):
    snapshot: dict
    full_schema: str
    chosen_schema: str
    schema_selection: dict
    schema_linking: dict
    schema_observation: dict
    schema_routing: dict
    trace: list[dict]
    node_metrics: dict
    generated_sql: str
    generation_success: bool
    execution_ok: bool
    execution_error_type: str | None
    execution_error_message: str | None
    execution_time_ms: float | None
    output_truncated: bool | None
    attempts: int
    error_type: str | None
    error_message: str | None
    status: str
    dry_run: bool
    schema_tables: list[str]
    sql_repair_enabled: bool
    original_sql: str
    current_sql: str
    repaired_sql: str
    repair_attempts: int
    repair_trigger: bool
    repair_error_type: str | None
    repair_success: bool | None
    repair_execution_recovered: bool
    repair_status: str
    initial_execution_ok: bool
    error_category: str | None
    execution_history: list[dict]
    generation_model_calls: int
    last_execution_time_ms: float | None


def _stats(model, started):
    return model_call_metrics(model, started, time.monotonic())


def _entry(node, started, **summary):
    return {"node": node, "elapsed_ms": round((time.monotonic() - started) * 1000, 3), **summary}


def build_schema_graph(model, *, schema_linking=True, sql_timeout=8.0, max_rows=1000,
                       max_prompt_chars=200000, dry_run=False, sql_repair=False, adaptive_linking=None):
    """Explicit input whitelist; no Gold/evaluator, SDK construction or retry policy."""
    if (type(schema_linking) is not bool or type(dry_run) is not bool or type(sql_repair) is not bool
            or type(max_prompt_chars) is not int or max_prompt_chars <= 0
            or type(max_rows) is not int or not 1 <= max_rows <= 10000
            or not math.isfinite(sql_timeout) or sql_timeout <= 0):
        raise ValueError("Invalid schema graph configuration")
    if getattr(getattr(model, "delegate", model), "max_retries", 0) != 0:
        raise ValueError("Schema graph requires an adapter with application retries disabled")
    gate = AdaptiveSchemaGate() if adaptive_linking is None else adaptive_linking
    if not isinstance(gate, AdaptiveSchemaGate) or (gate.enabled and (not schema_linking or sql_repair)):
        raise ValueError("Adaptive Gate is experimental and restricted to Schema-only without Repair")

    def inspect(state):
        started = time.monotonic()
        initial = {"trace": [], "node_metrics": {}, "generated_sql": "", "generation_success": False,
                   "execution_ok": False, "execution_error_type": None, "execution_error_message": None,
                   "execution_time_ms": None, "output_truncated": None, "attempts": 0,
                   "error_type": None, "error_message": None, "status": "failed", "dry_run": dry_run,
                   "sql_repair_enabled": sql_repair, "original_sql": "", "current_sql": "", "repaired_sql": "",
                   "repair_attempts": 0, "repair_trigger": False, "repair_error_type": None,
                   "repair_success": None, "repair_execution_recovered": False, "repair_status": "not_attempted",
                   "initial_execution_ok": False, "error_category": None, "execution_history": [],
                   "generation_model_calls": 0, "last_execution_time_ms": None,
                   "schema_routing": route_schema(None, gate, linking_enabled=schema_linking)}
        try:
            snapshot = read_schema(state["db_path"])
            initial.update(snapshot=snapshot, full_schema=snapshot["full_schema"],
                           trace=[_entry("inspect", started, tool="read_schema", tool_calls=1,
                                         backend="local_sqlite", read_only=True,
                                         table_count=len(snapshot["tables"]),
                                         schema_chars=len(snapshot["full_schema"]))])
        except Exception as exc:
            category, message = error_info(exc, "data")
            initial.update(error_type=category, error_message=message,
                           trace=[_entry("inspect", started, tool="read_schema", tool_calls=1,
                                         read_only=True, error_category=category)])
        return initial

    def link(state):
        started = time.monotonic()
        snapshot = state["snapshot"]
        routing = route_schema(snapshot, gate, linking_enabled=schema_linking)
        enabled = routing["routing_decision"] == "USE_LINKING" and not dry_run
        stats = {"model_calls": 0, "latency_ms": 0.0, "prompt_tokens": None,
                 "completion_tokens": None, "usage_reported_calls": 0, "request_attempt_ids": []}
        valid, fallback, reason = None, False, "disabled" if not schema_linking else "dry_run" if dry_run else None
        if gate.enabled and routing["linking_skipped"]:
            reason = "adaptive_gate_skip"
        selected = full_selection(snapshot)
        error = {}
        if enabled:
            system, user = linking_prompt(state["question"], state.get("evidence", ""), snapshot)
            if len(system) + len(user) > max_prompt_chars:
                fallback, valid, reason = True, False, "link_prompt_limit"
            else:
                called = time.monotonic()
                try:
                    text = model.ask(system, user)
                    selected = validate_selection(text, snapshot)
                    valid = True
                except SelectionError as exc:
                    valid, fallback, reason = False, True, str(exc)
                except ModelFailure as exc:
                    if exc.category in {"empty_response", "model_output_truncated"}:
                        valid, fallback, reason = False, True, exc.category
                    else:
                        reason = exc.category
                        error = {"error_type": exc.category, "error_message": exc.category}
                except Exception:
                    reason = "model_error"
                    error = {"error_type": reason, "error_message": reason}
                finally:
                    stats = _stats(model, called)
        chosen = render_selection(selected, snapshot)
        routing.update(selected_schema_size=len(chosen.encode("utf-8")),
                       actual_linking_attempts=stats["model_calls"], linking_attempt_ids=stats["request_attempt_ids"])
        metrics = dict(schema_metrics(state["full_schema"], chosen), enabled=schema_linking,
                       selection_attempted=stats["model_calls"] > 0, selection_valid=valid,
                       fallback=fallback, fallback_reason=reason, model_calls=stats["model_calls"],
                       latency_ms=stats["latency_ms"], prompt_tokens=stats["prompt_tokens"],
                       completion_tokens=stats["completion_tokens"], usage_reported_calls=stats["usage_reported_calls"])
        return {"chosen_schema": chosen, "schema_selection": selected, "schema_linking": metrics,
                "schema_routing": routing,
                "schema_observation": observe_schema(chosen, selected,
                    full_schema_sha256=metrics["full_schema_sha256"], fallback_reason=reason),
                "schema_tables": list(selected["tables"]),
                "node_metrics": {**state["node_metrics"], "schema_linking": stats}, **error,
                "trace": state["trace"] + [_entry("schema_linking", started, model_calls=stats["model_calls"],
                           selection_valid=valid, fallback=fallback, reason=reason,
                           selected_table_count=len(selected["tables"]), selected_chars=len(chosen))]}

    def generate(state):
        started = time.monotonic()
        system, user = generation_prompt(state["question"], state.get("evidence", ""), state["chosen_schema"])
        if len(system) + len(user) > max_prompt_chars:
            return {"error_type": "data_error", "error_message": "Validated schema prompt exceeds character limit",
                    "trace": state["trace"] + [_entry("generate", started, model_calls=0, error_category="data_error")]}
        if dry_run:
            return {"status": "dry_run_ok", "generation_success": None, "execution_ok": None,
                    "trace": state["trace"] + [_entry("generate", started, model_calls=0, dry_run=True)]}
        update = {}
        try:
            sql = extract_sql(model.ask(system, user))
            if not sql:
                raise ModelFailure("empty_response")
            update.update(generated_sql=sql, original_sql=sql, current_sql=sql, generation_success=True)
        except Exception as exc:
            category, message = error_info(exc, "model")
            update.update(error_type=category, error_message=message)
        stats = _stats(model, started)
        return {**update, "generation_model_calls": stats["model_calls"],
                "node_metrics": {**state["node_metrics"], "generation": stats},
                "trace": state["trace"] + [_entry("generate", started, model_calls=stats["model_calls"],
                          prompt_chars=len(system) + len(user),
                          schema_sha256=state["schema_linking"]["selected_schema_sha256"],
                          error_category=update.get("error_type"))]}

    def execute(state):
        started = time.monotonic()
        result = {"attempts": 1}
        try:
            output = execute_select(state["db_path"], state["generated_sql"],
                                    timeout_seconds=sql_timeout, max_rows=max_rows)
            result.update(execution_ok=True, initial_execution_ok=True,
                          output_truncated=not output["complete"], status="execution_succeeded")
        except Exception as exc:
            category, message = error_info(exc, "execute")
            result.update(error_type=category, error_message=message,
                          execution_error_type=category, execution_error_message=message)
        result["execution_time_ms"] = round((time.monotonic() - started) * 1000, 3)
        result["trace"] = state["trace"] + [_entry("execute", started, tool="execute_select", tool_calls=1,
                                                  read_only=True, execution_ok=result.get("execution_ok", False),
                                                  error_category=result.get("error_type"))]
        return redact(result)

    def validate(state):
        started = time.monotonic()
        update = {"error_type": None, "error_message": None, "error_category": None,
                  "execution_error_type": None, "execution_error_message": None, "execution_ok": False,
                  "last_execution_time_ms": None}
        try:
            validate_candidate(state["current_sql"])
        except Exception as exc:
            update.update(failure_fields(classify_error(exc, state["current_sql"])))
        return {**update, "trace": state["trace"] + [_entry("validate", started, tool_calls=0,
                                        error_category=update["error_category"])]}

    def execute_bounded(state):
        started = time.monotonic()
        update = {"attempts": state["attempts"] + 1}
        try:
            output = execute_select(state["db_path"], state["current_sql"],
                                    timeout_seconds=sql_timeout, max_rows=max_rows)
            update.update(execution_ok=True, output_truncated=not output["complete"],
                          status="execution_succeeded")
        except Exception as exc:
            update.update(failure_fields(classify_error(exc, state["current_sql"])))
        update["last_execution_time_ms"] = round((time.monotonic() - started) * 1000, 3)
        update["execution_time_ms"] = (state.get("execution_time_ms") or 0) + update["last_execution_time_ms"]
        return {**update, "trace": state["trace"] + [_entry("execute", started, tool="execute_select",
                     tool_calls=1, read_only=True, execution_ok=update.get("execution_ok", False),
                     error_category=update.get("error_category"))]}

    def classify(state):
        started = time.monotonic()
        repaired = state["repair_attempts"] > 0
        eligible = state.get("error_category") in REPAIRABLE and not repaired
        history = {"phase": "repair" if repaired else "initial", "sql": state["current_sql"],
                   "execution_ok": state["execution_ok"], "error_category": state.get("error_category"),
                   "execution_time_ms": state["last_execution_time_ms"],
                   "request_attempt_ids": state["node_metrics"].get("repair" if repaired else "generation", {})
                       .get("request_attempt_ids", []),
                   "validation_passed": not state.get("error_type") or state["attempts"] > int(repaired)}
        update = {"execution_history": state["execution_history"] + [history]}
        if not repaired:
            update.update(initial_execution_ok=state["execution_ok"], repair_trigger=eligible,
                          repair_error_type=state.get("error_category") if eligible else None)
        else:
            # Execution recovery is measurable here; semantic correctness is not.
            update.update(repair_execution_recovered=state["execution_ok"],
                          repair_success=None if state["execution_ok"] else False,
                          repair_status="execution_recovered_semantics_unverified" if state["execution_ok"] else "failed")
        return {**update, "trace": state["trace"] + [_entry("classify", started, repair_eligible=eligible,
                                                       error_category=state.get("error_category"))]}

    def repair(state):
        started = time.monotonic()
        update = {"repair_attempts": 1, "repair_status": "failed", "repair_success": False}
        try:
            system, user = repair_prompt(state["question"], state.get("evidence", ""), state["chosen_schema"],
                                         state["original_sql"], state["repair_error_type"])
            if len(system) + len(user) > max_prompt_chars:
                raise ModelFailure("model_prompt_limit")
            sql = extract_sql(model.ask(system, user))
            if not sql:
                raise ModelFailure("empty_response")
            update.update(repaired_sql=sql, current_sql=sql, generated_sql=sql,
                          error_type=None, error_message=None, error_category=None, repair_status="generated")
        except Exception as exc:
            category, message = error_info(exc, "model")
            update.update(error_type=category, error_message=message, error_category=category, status="failed")
        stats = (_stats(model, started) if update.get("error_type") != "model_prompt_limit" else
                 {"model_calls": 0, "latency_ms": 0.0, "prompt_tokens": None, "completion_tokens": None,
                  "usage_reported_calls": 0, "request_attempt_ids": []})
        return {**update, "node_metrics": {**state["node_metrics"], "repair": stats},
                "trace": state["trace"] + [_entry("repair", started, model_calls=stats["model_calls"],
                    trigger=state["repair_error_type"], error_category=update.get("error_type"))]}

    graph = StateGraph(SchemaState, input_schema=GraphInput)
    for name, fn in (("inspect", inspect), ("schema_linking", link), ("generate", generate),
                     ("execute", execute_bounded if sql_repair else execute)):
        graph.add_node(name, fn)
    graph.add_edge(START, "inspect")
    graph.add_conditional_edges("inspect", lambda s: "end" if s.get("error_type") else "link",
                                {"end": END, "link": "schema_linking"})
    graph.add_conditional_edges("schema_linking", lambda s: "end" if s.get("error_type") else "generate",
                                {"end": END, "generate": "generate"})
    target = "validate" if sql_repair else "execute"
    graph.add_conditional_edges("generate", lambda s: "end" if s.get("error_type") or dry_run else "next",
                                {"end": END, "next": target})
    if sql_repair:
        graph.add_node("validate", validate)
        graph.add_node("classify", classify)
        graph.add_node("repair", repair)
        graph.add_conditional_edges("validate", lambda s: "classify" if s.get("error_type") else "execute",
                                    {"classify": "classify", "execute": "execute"})
        graph.add_edge("execute", "classify")
        graph.add_conditional_edges("classify", lambda s: "repair" if s["repair_trigger"] and
                                    s["repair_attempts"] == 0 else "end", {"repair": "repair", "end": END})
        graph.add_conditional_edges("repair", lambda s: "end" if s.get("error_type") else "validate",
                                    {"end": END, "validate": "validate"})
    else:
        graph.add_edge("execute", END)
    return graph.compile()


def schema_graph_record(output):
    """Persist validated schema evidence, never complete prompts or snapshots."""
    fields = ("generated_sql", "generation_success", "execution_ok", "execution_error_type", "execution_error_message",
              "execution_time_ms", "output_truncated", "attempts", "error_type", "error_message", "status", "dry_run",
              "schema_tables", "schema_selection", "schema_linking", "schema_observation", "schema_routing", "node_metrics", "trace",
              "sql_repair_enabled", "original_sql", "current_sql", "repaired_sql", "repair_attempts",
              "repair_trigger", "repair_error_type", "repair_success", "repair_execution_recovered", "repair_status",
              "initial_execution_ok", "error_category", "execution_history", "generation_model_calls")
    record = {key: output[key] for key in fields if key in output}
    if output.get("dry_run"):
        record["preflight_ok"] = output.get("status") == "dry_run_ok"
    return redact(record)
