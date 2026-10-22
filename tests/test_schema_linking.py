"""Schema/graph unit tests use synthetic SQLite only, never benchmark replacements."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from bird_starter.baseline import run_baseline
from bird_starter.data import AgentInput
from bird_starter.model import Model, ModelFailure
from bird_starter.request_audit import RequestLog, inspect_request_log
from bird_starter.schema_linking import (SelectionError, read_schema, validate_selection, render_selection,
                                         full_selection, schema_metrics)
from bird_starter.workflow import build_schema_graph


@pytest.fixture
def relational_db(tmp_path):
    path = tmp_path / "relational-fixture.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('''
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, unused1 TEXT, unused2 TEXT, unused3 TEXT);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), amount INTEGER,
                    unused1 TEXT, unused2 TEXT, unused3 TEXT);
CREATE TABLE items(id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), sku TEXT, unused TEXT);
CREATE TABLE unrelated(id INTEGER PRIMARY KEY, unused1 TEXT, unused2 TEXT, unused3 TEXT, unused4 TEXT);
''')
    return path


def choice(tables, joins=None):
    return json.dumps({"tables": [{"name": t, "columns": c} for t, c in tables.items()], "joins": joins or []})


class Scripted:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.prompts = []

    def ask(self, system, user):
        self.prompts.append((system, user))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def initial(path):
    return AgentInput("fixture question", "fixture evidence", str(path), "schema_only").to_state()


def test_selection_retains_fk_pk_and_original_ddl(relational_db):
    snapshot = read_schema(relational_db)
    before = deepcopy(snapshot)
    data = relational_db.read_bytes()
    selected = validate_selection(choice({"ORDERS": ["AMOUNT"]}), snapshot)
    assert selected["tables"] == {"customers": ["id"], "orders": ["amount", "customer_id", "id"]}
    assert selected["added_tables"] == ["customers"]
    rendered = render_selection(selected, snapshot)
    assert 'FOREIGN KEY ("customer_id") REFERENCES "customers" ("id")' in rendered
    assert 'PRIMARY KEY ("id")' in rendered
    metrics = schema_metrics(snapshot["full_schema"], rendered)
    assert metrics["compression_ratio"] > 0
    assert metrics["full_schema_chars"] == len(snapshot["full_schema"])
    assert metrics["selected_schema_tokens_estimate"] == (len(rendered.encode()) + 3) // 4
    assert snapshot == before and relational_db.read_bytes() == data


def test_bridge_table_added_for_multihop_join(relational_db):
    snapshot = read_schema(relational_db)
    selected = validate_selection(choice({"items": ["sku"], "customers": ["name"]}), snapshot)
    assert set(selected["tables"]) == {"items", "orders", "customers"}
    assert "orders" in selected["added_tables"]
    assert {"id", "customer_id"} <= set(selected["tables"]["orders"])
    assert "order_id" in selected["tables"]["items"]


def test_composite_implicit_parent_pk(tmp_path):
    path = tmp_path / "composite-fixture.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('CREATE TABLE parent(a INT,b INT,n TEXT,PRIMARY KEY(a,b));'
                           'CREATE TABLE child(x INT,y INT,value TEXT,FOREIGN KEY(x,y) REFERENCES parent);')
    snapshot = read_schema(path)
    selected = validate_selection(choice({"child": ["value"]}), snapshot)
    assert selected["tables"]["parent"] == ["a", "b"]
    assert selected["tables"]["child"] == ["value", "x", "y"]
    assert snapshot["relations"][0]["right_columns"] == ["a", "b"]


@pytest.mark.parametrize("response", ["no json", "```json\n{}\n```", "null", "[]", '{}',
    '{"tables":[]}', '{"tables":["orders"]}', '{"tables":[{"name":"orders","columns":[]}]}',
    '{"tables":[{"name":"missing","columns":["id"]}]}',
    '{"tables":[{"name":"orders","columns":["missing"]}]}',
    '{"tables":[{"name":"orders","columns":["id"]}],"SQL":"must reject"}',
    '{"tables":[],"tables":[]}',
    choice({"customers": ["name"], "unrelated": ["id"]}),
    choice({"orders": ["id"]}, [{"left_table": "orders", "left_column": "missing",
                                   "right_table": "orders", "right_column": "id"}]),
])
def test_invalid_selection_falls_back_full_schema(relational_db, response):
    snapshot = read_schema(relational_db)
    with pytest.raises(SelectionError):
        validate_selection(response, snapshot)
    model = Scripted(response, "SELECT 1")
    output = build_schema_graph(model).invoke(initial(relational_db))
    assert output["schema_linking"]["fallback"] and output["schema_linking"]["selection_valid"] is False
    assert output["chosen_schema"] == output["full_schema"] == snapshot["full_schema"]
    assert output["schema_linking"]["compression_ratio"] == 0
    assert output["execution_ok"] and len(model.prompts) == 2


def test_non_fk_join_endpoints_are_column_validated(relational_db):
    snapshot = read_schema(relational_db)
    selected = validate_selection(choice({"customers": ["name"], "unrelated": ["unused1"]}, [
        {"left_table": "customers", "left_column": "id", "right_table": "unrelated", "right_column": "id"}]), snapshot)
    assert "id" in selected["tables"]["unrelated"]
    assert selected["relationships"][0]["kind"] == "model_join_columns_validated"
    assert "JOIN candidate" in render_selection(selected, snapshot)


def test_graph_no_repair_trace_and_gold_firewall(relational_db):
    marker = "GOLD_EVALUATION_ONLY_SENTINEL"
    model = Scripted(choice({"orders": ["amount"]}), "SELECT missing FROM orders")
    graph = build_schema_graph(model)
    assert "repair" not in graph.get_graph().nodes
    states = list(graph.stream({**initial(relational_db), "SQL": marker, "gold_sql": marker,
                                "chosen_schema": marker, "snapshot": {"gold": marker}}, stream_mode="values"))
    output = states[-1]
    assert output["error_type"] == "sql_schema_error" and output["attempts"] == 1
    assert len(model.prompts) == 2
    assert marker not in json.dumps(states) + json.dumps(model.prompts)
    assert all("SQL" not in state and "gold_sql" not in state for state in states)
    assert [t["node"] for t in output["trace"]] == ["inspect", "schema_linking", "generate", "execute"]
    assert "fixture question" not in json.dumps(output["trace"])
    assert "fixture evidence" in model.prompts[0][1]
    assert output["node_metrics"]["schema_linking"]["model_calls"] == 1
    assert output["node_metrics"]["generation"]["model_calls"] == 1


def test_disabled_linking_matches_baseline_generator_prompt(relational_db):
    model, baseline_model = Scripted("SELECT 1"), Scripted("SELECT 1")
    output = build_schema_graph(model, schema_linking=False).invoke(initial(relational_db))
    run_baseline(AgentInput.from_case({"question": "fixture question", "evidence": "fixture evidence"},
                                     relational_db, "baseline"), baseline_model)
    assert model.prompts == baseline_model.prompts
    assert output["schema_linking"]["model_calls"] == 0
    assert output["schema_linking"]["selection_valid"] is None


@pytest.mark.parametrize("category", ["model_authentication_error", "model_budget_exceeded", "model_audit_error", "model_timeout"])
def test_system_failure_never_falls_back_to_another_model_call(relational_db, category):
    model = Scripted(ModelFailure(category), "SELECT 1")
    output = build_schema_graph(model).invoke(initial(relational_db))
    assert output["error_type"] == category and len(model.prompts) == 1
    assert output["attempts"] == 0 and output["generated_sql"] == ""


@pytest.mark.parametrize("category", ["empty_response", "model_output_truncated"])
def test_content_failure_can_fallback_without_retry(relational_db, category):
    model = Scripted(ModelFailure(category), "SELECT 1")
    output = build_schema_graph(model).invoke(initial(relational_db))
    assert output["schema_linking"]["fallback_reason"] == category
    assert output["execution_ok"] and len(model.prompts) == 2
    assert model.prompts[0] != model.prompts[1]  # Linking + generation, not replay.


def test_dry_run_and_prompt_limits_do_not_call_model(relational_db):
    model = Scripted()
    output = build_schema_graph(model, dry_run=True).invoke(initial(relational_db))
    assert output["status"] == "dry_run_ok" and not model.prompts
    output = build_schema_graph(model, max_prompt_chars=1).invoke(initial(relational_db))
    assert output["error_type"] == "data_error" and not model.prompts


@pytest.mark.parametrize("budget,expected_calls", [(1, 1), (2, 2)])
def test_real_adapter_shared_audit_budget_across_nodes(relational_db, tmp_path, budget, expected_calls):
    responses = iter([choice({"orders": ["amount"]}), "SELECT 1"])
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return NS(choices=[NS(message=NS(content=next(responses)), finish_reason="stop")])
    path = tmp_path / "graph.attempts.jsonl"
    log = RequestLog(path, "fixture-run", "fixture", budget)
    model = Model(model="fixture", client=NS(chat=NS(completions=NS(create=create))),
                  max_calls=budget, max_retries=0, attempt_log=log)
    model.set_case_id(9)
    try:
        output = build_schema_graph(model).invoke(initial(relational_db))
        assert len(calls) == expected_calls
        report = inspect_request_log(path, "fixture-run", budget)
        assert report["prepared_attempts"] == expected_calls and report["automatic_requests_available"] == 0
        if budget == 1:
            assert output["error_type"] == "model_budget_exceeded" and output["attempts"] == 0
        else:
            assert output["execution_ok"]
    finally:
        log.close()


def test_graph_preserves_sql_safety(relational_db):
    before = relational_db.read_bytes()
    output = build_schema_graph(Scripted(choice({"orders": ["amount"]}), "DROP TABLE orders")).invoke(initial(relational_db))
    assert output["error_type"] == "sql_safety_error" and relational_db.read_bytes() == before


def test_graph_executes_join_with_only_validated_projection(relational_db):
    before = relational_db.read_bytes()
    model = Scripted(choice({"orders": ["amount"], "customers": ["name"]}),
        "SELECT c.name,SUM(o.amount) FROM orders o JOIN customers c ON o.customer_id=c.id GROUP BY c.name")
    output = build_schema_graph(model).invoke(initial(relational_db))
    assert output["execution_ok"] and output["attempts"] == 1 and len(model.prompts) == 2
    assert output["chosen_schema"] in model.prompts[1][1]
    assert "unrelated" not in model.prompts[1][1] and "unused1" not in model.prompts[1][1]
    assert "customer_id" in output["schema_selection"]["tables"]["orders"]
    assert relational_db.read_bytes() == before


@pytest.mark.parametrize("kwargs", [{"max_rows": 0}, {"sql_timeout": 0}, {"max_prompt_chars": False}])
def test_graph_configuration_rejected_before_model_use(kwargs):
    with pytest.raises(ValueError):
        build_schema_graph(Scripted(), **kwargs)


def test_graph_rejects_retrying_adapter():
    with pytest.raises(ValueError, match="retries disabled"):
        build_schema_graph(NS(max_retries=1))


def test_full_coverage_still_keeps_validated_join_hints(relational_db):
    snapshot = read_schema(relational_db)
    all_fields = {t: [c["name"] for c in item["columns"]] for t, item in snapshot["tables"].items()}
    selected = validate_selection(choice(all_fields, [{"left_table": "customers", "left_column": "id",
                                                      "right_table": "unrelated", "right_column": "id"}]), snapshot)
    text = render_selection(selected, snapshot)
    assert text.startswith(snapshot["full_schema"]) and "JOIN candidate" in text
    # An expanded hint is allowed to yield negative compression; never clamp it to zero.
    assert schema_metrics(snapshot["full_schema"], text)["compression_ratio"] < 0
