"""LangGraph: schema -> optional prune -> draft -> execute -> optional repair."""
from __future__ import annotations
import json
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from .tools import schema_catalog, execute_select
from .model import Model, extract_sql
from .prompts import generation_prompt
from .schema_workflow import build_schema_graph

class State(TypedDict, total=False):
    question: str
    evidence: str
    db_path: str
    mode: str
    catalog: dict[str, str]
    chosen_schema: str
    sql: str
    error: str
    attempts: int
    preview: list
    elapsed_ms: float
    complete: bool


def build_graph(model: Model, max_repairs: int = 1, *, sql_timeout: float = 8.0, max_rows: int = 20):
    def inspect(state: State) -> dict:
        return {"catalog": schema_catalog(state["db_path"]), "attempts": 0}

    def select_schema(state: State) -> dict:
        catalog = state["catalog"]
        if state.get("mode") != "graph":
            return {"chosen_schema": "\n".join(catalog.values())}
        names = list(catalog)
        response = model.ask(
            "You select database tables for Text-to-SQL. Return ONLY JSON: {\"tables\":[\"name\"]}."
            " Prefer recall; include all tables needed for JOINs.",
            f"Question: {state['question']}\nAvailable table names: {json.dumps(names, ensure_ascii=False)}\n"
            f"Full DDL:\n" + "\n".join(catalog.values()),
        )
        try:
            parsed = json.loads(response)
            selected = [x for x in parsed["tables"] if x in catalog]
        except (ValueError, KeyError, TypeError):
            selected = []
        # Fallback ensures a formatting error doesn't deprive the generator of schema.
        if not selected:
            selected = names
        return {"chosen_schema": "\n".join(catalog[x] for x in selected)}

    def draft(state: State) -> dict:
        sql = extract_sql(model.ask(*generation_prompt(
            state["question"], state.get("evidence", ""), state["chosen_schema"])))
        return {"sql": sql, "error": ""}

    def run_sql(state: State) -> dict:
        attempt = state.get("attempts", 0) + 1
        try:
            result = execute_select(state["db_path"], state["sql"], max_rows=max_rows, timeout_seconds=sql_timeout)
            return {"attempts": attempt, "error": "", "preview": result["rows"],
                    "elapsed_ms": result["elapsed_ms"], "complete": result["complete"]}
        except Exception as exc:
            return {"attempts": attempt, "error": f"{type(exc).__name__}: {exc}",
                    "preview": [], "complete": False}

    def repair(state: State) -> dict:
        sql = extract_sql(model.ask(
            "Repair one read-only SQLite SELECT statement using this error. "
            "Return only corrected SQL, without commentary.",
            f"Schema:\n{state['chosen_schema']}\nQuestion: {state['question']}\n"
            f"Evidence: {state.get('evidence','')}\nPrevious SQL: {state['sql']}\n"
            f"Execution error: {state['error']}",
        ))
        return {"sql": sql, "error": ""}

    def route(state: State) -> str:
        return "repair" if (state.get("mode") == "graph" and state.get("error")
                             and state.get("attempts", 0) <= max_repairs) else "finish"

    builder = StateGraph(State)
    for name, fn in (("inspect", inspect), ("select_schema", select_schema),
                     ("draft", draft), ("run_sql", run_sql), ("repair", repair)):
        builder.add_node(name, fn)
    builder.add_edge(START, "inspect")
    builder.add_edge("inspect", "select_schema")
    builder.add_edge("select_schema", "draft")
    builder.add_edge("draft", "run_sql")
    builder.add_conditional_edges("run_sql", route, {"repair": "repair", "finish": END})
    builder.add_edge("repair", "run_sql")
    return builder.compile()
