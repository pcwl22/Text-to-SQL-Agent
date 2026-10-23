"""Adaptive Gate tests use synthetic SQLite and injected SDK fixtures only."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from bird_starter.ablation import GroupRequestLog
from bird_starter.data import AgentInput
from bird_starter.model import Model, TrackedModel
from bird_starter.request_audit import RequestLog, inspect_request_log
from bird_starter.schema_linking import read_schema, render_selection
from bird_starter.schema_routing import AdaptiveSchemaGate, load_gate, route_schema
from bird_starter.schema_workflow import build_schema_graph, schema_graph_record

LINK = '{"tables":[{"name":"orders","columns":["amount"]}]}'
SQL = "SELECT SUM(amount) FROM orders"


class Scripted:
    def __init__(self, *responses):
        self.responses, self.prompts = iter(responses), []

    def ask(self, system, user):
        self.prompts.append((system, user))
        return next(self.responses)


def state(path, mode="schema_only"):
    return {**AgentInput("fixture question", "fixture clue", str(path), mode).to_state(),
            "SQL": "GOLD_GATE_FIXTURE_SENTINEL", "official_ex": 0,
            "schema_routing": {"routing_decision": "UNTRUSTED"}}


def test_disabled_gate_preserves_prompts_and_original_link_path(tiny_db):
    legacy, disabled = Scripted(LINK, SQL), Scripted(LINK, SQL)
    original = build_schema_graph(legacy).invoke(state(tiny_db[1]))
    result = build_schema_graph(disabled, adaptive_linking=AdaptiveSchemaGate(False, 1)).invoke(state(tiny_db[1]))
    assert legacy.prompts == disabled.prompts and len(disabled.prompts) == 2
    for field in ("chosen_schema", "generated_sql", "schema_selection", "execution_ok", "repair_attempts", "attempts"):
        assert result[field] == original[field]
    assert result["schema_routing"]["routing_reason"] == "gate_disabled"
    assert result["schema_routing"]["actual_linking_attempts"] == 1


@pytest.mark.parametrize("offset,skip", [(-1, False), (0, True), (1, True)])
def test_boundary_and_actual_linking_calls(tiny_db, offset, skip):
    snapshot = read_schema(tiny_db[1])
    gate = AdaptiveSchemaGate(True, len(snapshot["full_schema"].encode()) + offset)
    model = Scripted(*([SQL] if skip else [LINK, SQL]))
    result = build_schema_graph(model, adaptive_linking=gate).invoke(state(tiny_db[1]))
    route = result["schema_routing"]
    assert route["linking_skipped"] is skip
    assert route["actual_linking_attempts"] == int(not skip)
    assert len(model.prompts) == 1 + int(not skip)
    assert result["execution_ok"] and not result["repair_trigger"]
    assert result["schema_linking"]["selection_attempted"] is (not skip)
    assert not result["schema_linking"]["fallback"]
    if skip:
        assert result["chosen_schema"] == snapshot["full_schema"]
        assert set(result["schema_selection"]["tables"]) == set(snapshot["tables"])
    record = schema_graph_record(result)
    evidence = record["schema_observation"]
    assert render_selection(evidence["selection"], snapshot) == evidence["selected_schema"]
    assert route["full_schema_size"] == len(snapshot["full_schema"].encode())
    assert route["selected_schema_size"] == len(evidence["selected_schema"].encode())
    assert "GOLD_GATE_FIXTURE_SENTINEL" not in json.dumps(record) + json.dumps(model.prompts)
    assert "UNTRUSTED" not in json.dumps(record)


def test_gate_uses_utf8_bytes_not_characters():
    snap = {"full_schema": 'CREATE TABLE "表"("列" INT)', "tables": {"表": {"columns": [{"name": "列"}]}}}
    assert route_schema(snap, AdaptiveSchemaGate(True, len(snap["full_schema"])))['routing_decision'] == 'USE_LINKING'


@pytest.mark.parametrize("enabled,threshold", [(1, 10), ("true", 10), (True, None), (True, 0),
    (True, -1), (True, True), (True, 1.5), (False, -1), (False, float("inf"))])
def test_invalid_configuration(enabled, threshold):
    with pytest.raises(ValueError):
        AdaptiveSchemaGate(enabled, threshold)


@pytest.mark.parametrize("payload", [[], {}, {"version": "other"}])
def test_invalid_configuration_file(tmp_path, payload):
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_gate(path)


@pytest.mark.parametrize("options", [{"schema_linking": False}, {"sql_repair": True}, {"adaptive_linking": {}}])
def test_experimental_gate_does_not_silently_change_other_modes(options):
    kwargs = {"adaptive_linking": AdaptiveSchemaGate(True, 10), **options}
    with pytest.raises(ValueError):
        build_schema_graph(None, **kwargs)


def test_missing_schema_never_calls_model(tmp_path):
    model = Scripted()
    result = build_schema_graph(model, adaptive_linking=AdaptiveSchemaGate(True, 1000)).invoke(state(tmp_path / "missing.sqlite"))
    assert result["status"] == "failed" and result["error_type"] == "data_error"
    assert not model.prompts
    assert result["schema_routing"]["routing_decision"] == "NOT_RUN"
    assert result["schema_routing"]["actual_linking_attempts"] == 0


def test_large_schema_invalid_link_retains_original_fallback(tiny_db):
    model = Scripted('invalid JSON', SQL)
    result = build_schema_graph(model, adaptive_linking=AdaptiveSchemaGate(True, 1)).invoke(state(tiny_db[1]))
    assert len(model.prompts) == 2 and result["execution_ok"]
    assert result["schema_linking"]["fallback"] is True
    assert result["chosen_schema"] == read_schema(tiny_db[1])["full_schema"]
    assert result["schema_routing"]["routing_decision"] == "USE_LINKING"
    assert result["schema_routing"]["selected_schema_size"] == result["schema_routing"]["full_schema_size"]


def test_gate_skip_is_not_sql_safety_bypass(tiny_db):
    before = tiny_db[1].read_bytes()
    model = Scripted("DROP TABLE orders")
    result = build_schema_graph(model, adaptive_linking=AdaptiveSchemaGate(True, 100000)).invoke(state(tiny_db[1]))
    assert result["execution_ok"] is False and result["error_type"] == "sql_safety_error"
    assert tiny_db[1].read_bytes() == before and len(model.prompts) == 1


def sdk_harness(tmp_path, cap, responses):
    log = RequestLog(tmp_path / "attempts.jsonl", "fixture-run", "fixture", cap)
    scoped = GroupRequestLog(log, "B", cap)
    calls, answers = [], iter(responses)
    def create(**kwargs):
        events = [json.loads(s) for s in log.path.read_text().splitlines()]
        assert events[-1]["event"] == "ATTEMPT_PREPARED"
        calls.append(kwargs)
        return NS(status_code=200, choices=[NS(message=NS(content=next(answers)), finish_reason="stop")])
    adapter = Model(model="fixture", max_calls=cap, max_retries=0, attempt_log=scoped,
                    client=NS(chat=NS(completions=NS(create=create))))
    adapter.set_case_id(1)
    return log, calls, TrackedModel(adapter)


@pytest.mark.parametrize("skip,cap,expected", [(True, 1, 1), (False, 1, 1), (False, 2, 2), (True, 0, 0)])
def test_gate_uses_existing_shared_budget_and_prepared_audit(tmp_path, tiny_db, skip, cap, expected):
    log, calls, model = sdk_harness(tmp_path, cap, [SQL] if skip else [LINK, SQL])
    try:
        gate = AdaptiveSchemaGate(True, 100000 if skip else 1)
        result = build_schema_graph(model, adaptive_linking=gate).invoke(state(tiny_db[1]))
        audit = inspect_request_log(log.path, "fixture-run", cap)
        assert len(calls) == log.reserved == audit["prepared_attempts"] == expected
        assert result["schema_routing"]["actual_linking_attempts"] == int(not skip and cap > 0)
        assert result["schema_routing"]["linking_attempt_ids"] == ([] if skip else model.metrics["request_attempt_ids"][:1])
        if expected < 1 + int(not skip):
            assert result["error_type"] == "model_budget_exceeded"
        assert audit["automatic_requests_available"] == 0
    finally:
        log.close()


def test_failed_prepared_fsync_blocks_generator_after_skip(tmp_path, tiny_db, monkeypatch):
    import bird_starter.request_audit as audit
    log, calls, model = sdk_harness(tmp_path, 1, [SQL])
    monkeypatch.setattr(audit.os, "fsync", lambda *_: (_ for _ in ()).throw(OSError("fixture fsync")))
    try:
        result = build_schema_graph(model, adaptive_linking=AdaptiveSchemaGate(True, 100000)).invoke(state(tiny_db[1]))
        assert not calls and log.reserved == 1
        assert result["error_type"] == "model_audit_error"
    finally:
        log.close()


def test_dry_run_routes_without_model_or_sql(tiny_db, monkeypatch):
    monkeypatch.setattr("bird_starter.schema_workflow.execute_select", lambda *_a, **_k: pytest.fail("SQL executed"))
    for threshold, decision in ((1, "USE_LINKING"), (100000, "SKIP_LINKING")):
        model = Scripted()
        result = build_schema_graph(model, dry_run=True, adaptive_linking=AdaptiveSchemaGate(True, threshold)).invoke(state(tiny_db[1]))
        assert result["status"] == "dry_run_ok" and not model.prompts
        assert result["schema_routing"]["routing_decision"] == decision
        assert result["schema_routing"]["actual_linking_attempts"] == 0


def test_crash_after_prepared_cannot_restore_request_authorization(tmp_path, tiny_db):
    log_path = tmp_path / "crash.attempts.jsonl"
    script = '''
import os,socket,sys
from types import SimpleNamespace as NS
from bird_starter.data import AgentInput
from bird_starter.model import Model
from bird_starter.request_audit import RequestLog
from bird_starter.schema_routing import AdaptiveSchemaGate
from bird_starter.schema_workflow import build_schema_graph
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline'))
socket.socket.connect_ex=socket.socket.connect
log=RequestLog(sys.argv[2],'crash-fixture','fixture',1)
def create(**kw):os._exit(61)
model=Model(model='fixture',max_calls=1,max_retries=0,attempt_log=log,
            client=NS(chat=NS(completions=NS(create=create))))
model.set_case_id(1)
build_schema_graph(model,adaptive_linking=AdaptiveSchemaGate(True,100000)).invoke(
    AgentInput('fixture','',sys.argv[1],'schema_only').to_state())
'''
    child = subprocess.run([sys.executable, "-c", script, str(tiny_db[1]), str(log_path)],
                           env={**os.environ, "PYTHON_DOTENV_DISABLED": "1"}, capture_output=True, timeout=30)
    assert child.returncode == 61, child.stderr
    before = log_path.read_bytes()
    report = inspect_request_log(log_path, "crash-fixture", 1)
    assert report["prepared_attempts"] == report["indeterminate_attempts"] == 1
    assert report["automatic_requests_available"] == 0 and not report["automatic_resume_allowed"]
    with pytest.raises(FileExistsError):
        RequestLog(log_path, "crash-fixture", "fixture", 1)
    assert log_path.read_bytes() == before
