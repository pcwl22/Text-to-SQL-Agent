"""Offline repair tests. All invented SQL/databases below are unit-test fixtures."""
import json
import sqlite3
from types import SimpleNamespace as NS

import httpx
from openai import APIConnectionError, APITimeoutError, APIStatusError
import pytest

import run_eval
from bird_starter.data import AgentInput
from bird_starter.model import Model, ModelFailure, TrackedModel
from bird_starter.request_audit import RequestLog, inspect_request_log
from bird_starter.schema_workflow import build_schema_graph, schema_graph_record
from bird_starter.sql_repair import ErrorCategory, classify_error
from bird_starter.repair_reporting import repair_summary


def sdk_reply(content):
    return NS(status_code=200, _request_id="offline-fixture", model="fixture",
              choices=[NS(message=NS(content=content), finish_reason="stop")],
              usage=NS(prompt_tokens=12, completion_tokens=3))


def harness(tmp_path, responses, cap=10):
    log_path = tmp_path / "mock.attempts.jsonl"
    log = RequestLog(log_path, "fixture-run", "fixture", cap)
    answers, calls = iter(responses), []
    def create(**kwargs):
        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert events[-1]["event"] == "ATTEMPT_PREPARED"
        calls.append(kwargs)
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return sdk_reply(answer)
    model = Model(model="fixture", max_retries=0, max_calls=cap, attempt_log=log,
                  client=NS(chat=NS(completions=NS(create=create))))
    model.set_case_id(7)
    return model, log, log_path, calls


def state(db):
    return AgentInput("fixture question", "fixture evidence", str(db), "repair_only").to_state()


@pytest.mark.parametrize("original,category", [
    ("SELECT missing FROM orders", "missing_column"),
    ("SELECT * FROM absent", "missing_table"),
    ("SELECT FROM orders", "syntax"),
    ("SELECT id FROM orders a JOIN orders b ON a.id=b.id", "ambiguous_column"),
])
def test_repair_success_once_and_audit_association(tmp_path, tiny_db, original, category):
    model, log, path, calls = harness(tmp_path, [original, "SELECT amount FROM orders"])
    try:
        output = build_schema_graph(TrackedModel(model), schema_linking=False, sql_repair=True).invoke(state(tiny_db[1]))
        assert output["initial_execution_ok"] is False and output["execution_ok"] is True
        assert output["repair_execution_recovered"] is True and output["repair_success"] is None
        assert output["original_sql"] == original and output["repaired_sql"] == output["current_sql"] == "SELECT amount FROM orders"
        assert output["repair_error_type"] == category and output["repair_attempts"] == 1
        assert len(calls) == model.total_calls == 2
        report = inspect_request_log(path, "fixture-run", 10)
        ids = [a["attempt_id"] for a in report["attempts"]]
        assert [h["request_attempt_ids"] for h in output["execution_history"]] == [[ids[0]], [ids[1]]]
        assert all(a["result_status"] == "SUCCEEDED" for a in report["attempts"])
        assert all(a["case_id"] == 7 for a in report["attempts"])
        assert [e["node"] for e in output["trace"]].count("repair") == 1
        assert output["full_schema"] != "" and "Repair one" in calls[1]["messages"][0]["content"]
        prompt = json.loads(calls[1]["messages"][1]["content"])
        assert set(prompt) == {"question", "evidence", "schema", "original_sql", "error_category"}
        assert prompt["error_category"] == category
    finally:
        log.close()


@pytest.mark.parametrize("sql", ["SELECT amount FROM orders", "SELECT * FROM orders WHERE 0", "SELECT 999"])
def test_success_empty_or_semantically_wrong_query_never_repaired(tmp_path, tiny_db, sql):
    model, log, _, calls = harness(tmp_path, [sql])
    try:
        initial = dict(state(tiny_db[1]), SQL="GOLD_REPAIR_SENTINEL", local_result_match_proxy=False)
        graph = build_schema_graph(model, schema_linking=False, sql_repair=True)
        outputs = list(graph.stream(initial, stream_mode="values"))
        output = outputs[-1]
        assert output["execution_ok"] and output["repair_attempts"] == 0 and len(calls) == 1
        assert output["repair_success"] is None
        assert "GOLD_REPAIR_SENTINEL" not in json.dumps(outputs) + json.dumps(calls)
        assert all("SQL" not in value and "local_result_match_proxy" not in value for value in outputs)
    finally:
        log.close()


@pytest.mark.parametrize("sql", ["DROP TABLE orders", "DELETE FROM orders", "UPDATE orders SET amount=0",
    "SELECT FROM orders; DROP TABLE orders", "SELECT 1; SELECT 2", "SELECT load_extension('fixture')",
    'SELECT "writefile"( FROM orders', "PRAGMA user_version=10", "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x"])
def test_dangerous_or_ambiguous_sql_never_repaired(tmp_path, tiny_db, sql):
    before = tiny_db[1].read_bytes()
    model, log, _, calls = harness(tmp_path, [sql])
    try:
        output = build_schema_graph(model, schema_linking=False, sql_repair=True).invoke(state(tiny_db[1]))
        assert not output["execution_ok"] and output["error_category"] == "safety_rejected"
        assert output["repair_attempts"] == 0 and len(calls) == 1
        assert before == tiny_db[1].read_bytes()
    finally:
        log.close()


@pytest.mark.parametrize("repaired", ["SELECT still_missing FROM orders", "SELECT FROM orders", "DROP TABLE orders"])
def test_failed_repair_cannot_loop(tmp_path, tiny_db, repaired):
    model, log, _, calls = harness(tmp_path, ["SELECT missing FROM orders", repaired])
    try:
        output = build_schema_graph(model, schema_linking=False, sql_repair=True).invoke(state(tiny_db[1]), {"recursion_limit": 20})
        assert not output["execution_ok"] and output["repair_success"] is False
        assert output["repair_attempts"] == 1 and len(calls) == 2
        assert len(output["execution_history"]) == 2 and len(output["trace"]) <= 10
    finally:
        log.close()


def test_budget_exhaustion_blocks_repair_http(tmp_path, tiny_db):
    model, log, path, calls = harness(tmp_path, ["SELECT missing FROM orders"], cap=1)
    try:
        output = build_schema_graph(model, schema_linking=False, sql_repair=True).invoke(state(tiny_db[1]))
        assert output["error_type"] == "model_budget_exceeded"
        assert output["repair_attempts"] == 1 and output["node_metrics"]["repair"]["model_calls"] == 0
        assert len(calls) == model.total_calls == log.reserved == 1
        assert inspect_request_log(path, "fixture-run", 1)["prepared_attempts"] == 1
    finally:
        log.close()


@pytest.mark.parametrize("phase", ["generation", "repair"])
@pytest.mark.parametrize("error", ["empty_response", "model_output_truncated", "model_authentication_error",
                                   "model_network_error", "model_audit_error"])
def test_model_failure_never_triggers_another_request(tmp_path, tiny_db, phase, error):
    responses = (["SELECT missing FROM orders"] if phase == "repair" else []) + [ModelFailure(error)]
    model, log, _, calls = harness(tmp_path, responses)
    try:
        output = build_schema_graph(model, schema_linking=False, sql_repair=True).invoke(state(tiny_db[1]))
        assert output["error_type"] == error and not output["execution_ok"]
        assert len(calls) == (2 if phase == "repair" else 1)
    finally:
        log.close()


def test_query_timeout_no_repair(tmp_path, tiny_db):
    query = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<1000000000) SELECT sum(x) FROM c"
    model, log, _, calls = harness(tmp_path, [query])
    try:
        output = build_schema_graph(model, schema_linking=False, sql_repair=True, sql_timeout=.001).invoke(state(tiny_db[1]))
        assert not output["execution_ok"] and output["repair_attempts"] == 0 and len(calls) == 1
        assert output["error_category"] == "timeout"
    finally:
        log.close()


@pytest.mark.parametrize("code,diagnostic,expected", [
    (sqlite3.SQLITE_AUTH, "no such column: x", "safety_rejected"),
    (sqlite3.SQLITE_CORRUPT, "no such column: x", "database_failure"),
    (sqlite3.SQLITE_CANTOPEN, "no such column: x", "database_failure"),
    (sqlite3.SQLITE_ERROR, "custom function says no such column: x", "unknown_failure"),
    (sqlite3.SQLITE_ERROR, "no such column: x\nsecret", "unknown_failure"),
    (None, "no such column: x", "database_failure"),
])
def test_error_code_and_exact_diagnostic_required(code, diagnostic, expected):
    error = sqlite3.OperationalError(diagnostic)
    if code is not None:
        error.sqlite_errorcode = code
    assert classify_error(error, "SELECT amount FROM orders") == expected


@pytest.mark.parametrize("linking,repair,expected_calls", [(False, False, 1), (True, False, 2),
                                                          (False, True, 2), (True, True, 3)])
def test_feature_toggles_and_case_state_reset(tmp_path, tiny_db, linking, repair, expected_calls):
    choice = json.dumps({"tables": [{"name": "orders", "columns": ["amount"]}]})
    first = ([choice] if linking else []) + ["SELECT missing FROM orders"] + (["SELECT amount FROM orders"] if repair else [])
    second = ([choice] if linking else []) + ["SELECT 1"]
    model, log, _, calls = harness(tmp_path, first + second)
    try:
        graph = build_schema_graph(model, schema_linking=linking, sql_repair=repair)
        one = graph.invoke(state(tiny_db[1]))
        assert len(calls) == expected_calls and one["execution_ok"] == repair
        two = graph.invoke(state(tiny_db[1]))
        assert two["repair_attempts"] == 0 and two["repaired_sql"] == "" and two["execution_ok"]
        assert one["full_schema"] == two["full_schema"]
    finally:
        log.close()


def test_repair_audit_fsync_failure_blocks_sdk(tmp_path, tiny_db, monkeypatch):
    model, log, _, calls = harness(tmp_path, ["SELECT missing FROM orders", "SELECT 1"])
    original = log._append
    def append(event):
        if event["event"] == "ATTEMPT_PREPARED" and event["attempt_sequence"] == 2:
            from bird_starter.request_audit import AuditError
            log.poisoned = True
            raise AuditError()
        original(event)
    monkeypatch.setattr(log, "_append", append)
    try:
        output = build_schema_graph(model, schema_linking=False, sql_repair=True).invoke(state(tiny_db[1]))
        assert output["error_type"] == "model_audit_error" and len(calls) == 1
    finally:
        log.close()


@pytest.mark.parametrize("mode,linking,repair", [("baseline", False, False), ("schema_only", True, False),
                                                ("repair_only", False, True), ("full", True, True)])
def test_cli_four_groups_jsonl_summary_and_audit(tmp_path, tiny_db, monkeypatch, mode, linking, repair):
    cases = tmp_path / "fixture.json"
    cases.write_text(json.dumps([{"question_id": 7, "db_id": "demo", "question": "fixture", "evidence": "",
                                 "SQL": "GOLD_CLI_REPAIR_SENTINEL"}]))
    answers = iter(([json.dumps({"tables": [{"name": "orders", "columns": ["amount"]}]})] if linking else []) +
                   ["SELECT missing FROM orders"] + (["SELECT 1"] if repair else []))
    calls = []
    def create(**kw):
        calls.append(kw)
        return sdk_reply(next(answers))
    monkeypatch.setattr(run_eval, "Model", lambda **kw: Model(**kw, client=NS(chat=NS(completions=NS(create=create)))))
    # Deliberately wrong semantic result. Only the evaluator receives Gold, after Graph END.
    monkeypatch.setattr(run_eval, "result_match_proxy", lambda *args: False)
    out = tmp_path / "new.jsonl"
    args = ["--mode", mode, "--request-retries", "0", "--cases", str(cases), "--db-root", str(tiny_db[0]),
            "--model", "fixture", "--max-model-calls", "3", "--allow-api", "--out", str(out)]
    summary = run_eval.main(args)
    row = json.loads(out.read_text())
    assert row["execution_ok"] == repair and len(calls) == 1 + linking + repair
    assert summary["request_audit"]["prepared_attempts"] == len(calls) == row["model_calls"]
    assert "GOLD_CLI_REPAIR_SENTINEL" not in json.dumps(calls) + out.read_text()
    if repair:
        assert row["repair_success"] is False and row["repair_execution_recovered"]
        assert summary["sql_repair"]["repair_success_rate"] == 0
        assert summary["sql_repair"]["repair_execution_recovery_rate"] == 1
        assert summary["sql_repair"]["repair_prompt_tokens"] == 12
    before = {p.name: p.read_bytes() for p in tmp_path.glob('new.*')}
    with pytest.raises(SystemExit):
        run_eval.main(args)
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob('new.*')}


def test_repair_metrics_unknown_semantics_not_execution_accuracy():
    row = {"case_id": 1, "dry_run": False, "initial_execution_ok": False, "execution_ok": True,
           "repair_trigger": True, "repair_attempts": 1, "repair_success": None, "repair_execution_recovered": True,
           "model_calls": 2, "node_metrics": {"repair": {"model_calls": 1, "usage_reported_calls": 0}}}
    report = repair_summary([row])
    assert report["repair_execution_recovery_rate"] == 1 and report["repair_success_rate"] is None
    assert report["repair_success_unknown_cases"] == report["repair_usage_unknown_calls"] == 1
    assert report["repair_prompt_tokens"] is None


@pytest.mark.parametrize("code,message,category", [(sqlite3.SQLITE_AUTH, "not authorized", "safety_rejected"),
    (sqlite3.SQLITE_CANTOPEN, "cannot open", "database_failure"),
    (sqlite3.SQLITE_CORRUPT, "corrupt", "database_failure"),
    (sqlite3.SQLITE_ERROR, "user-defined function raised exception", "unknown_failure")])
def test_non_repairable_execution_error_route(tmp_path, tiny_db, monkeypatch, code, message, category):
    import bird_starter.schema_workflow as workflow
    def fail(*args, **kwargs):
        error = sqlite3.OperationalError(message)
        error.sqlite_errorcode = code
        raise error
    monkeypatch.setattr(workflow, "execute_select", fail)
    model, log, _, calls = harness(tmp_path, ["SELECT amount FROM orders"])
    try:
        output = build_schema_graph(model, schema_linking=False, sql_repair=True).invoke(state(tiny_db[1]))
        assert output["error_category"] == category
        assert output["repair_attempts"] == 0 and len(calls) == 1
    finally:
        log.close()


def test_repair_disabled_switch_and_zero_retry_requirement(tmp_path, tiny_db):
    model, log, _, calls = harness(tmp_path, ["SELECT missing FROM orders"])
    try:
        output = build_schema_graph(model, schema_linking=False).invoke(state(tiny_db[1]))
        assert output["repair_attempts"] == 0 and len(calls) == 1
        model.max_retries = 1
        with pytest.raises(ValueError, match="retries disabled"):
            build_schema_graph(model, schema_linking=False, sql_repair=True)
    finally:
        log.close()
