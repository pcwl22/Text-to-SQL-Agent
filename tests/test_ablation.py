"""Offline platform tests; tiny_db and all invented records are test fixtures only."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import httpx
from openai import APIConnectionError, APIStatusError
import pytest

import bird_starter.ablation as platform
from bird_starter.ablation import run_suite, GroupRequestLog, load_configuration
from bird_starter.ablation_reporting import summarize_group, paired_comparisons
from bird_starter.data import load_cases
from bird_starter.experiment_sets import file_sha256, make_dev_manifest
from bird_starter.model import Model, ModelFailure
from bird_starter.request_audit import RequestLog, inspect_experiment
from scripts.run_ablation import main


@pytest.fixture
def suite_files(tmp_path, tiny_db):
    cases = tmp_path / "fixture_cases.json"
    cases.write_text(json.dumps([{"question_id": n, "db_id": "demo", "question": "fixture amount",
        "evidence": "fixture clue", "difficulty": d, "SQL": "SELECT SUM(amount) FROM orders /* GOLD_ONLY_SUITE_SENTINEL */"}
        for n, d in enumerate(("simple", "moderate", "challenging"), 1)]))
    manifest = tmp_path / "fixture_manifest.json"
    manifest.write_text(json.dumps(make_dev_manifest(load_cases(cases), file_sha256(cases), "fixture-revision")))
    config = json.loads((platform.PROJECT_ROOT / "configs/phase3c/ablation_v1.json").read_text(encoding="utf-8"))
    config.update(manifest=str(manifest), manifest_sha256=file_sha256(manifest), dataset_version="fixture-revision",
                  dataset_sha256=file_sha256(cases), dataset_question_count=3, case_count=3, database_count=1,
                  database_sha256={"demo": file_sha256(tiny_db[1])}, source_profiles={}, batch_max_http_attempts=24)
    config["common"]["model"] = "fixture"
    for g, cap in zip("ABCD", (3, 6, 6, 9)):
        config["groups"][g]["max_http_attempts"] = cap
    path = tmp_path / "fixture_suite.json"
    path.write_text(json.dumps(config))
    return path, cases, tiny_db[0]


def records(folder, group):
    return [json.loads(line) for line in (folder / f"{group}.jsonl").read_text(encoding="utf-8").splitlines()]


class ScriptedSDK:
    """No case record/Gold accepted by this SDK interface."""
    def __init__(self, fault=None, wrong_sql=False):
        self.calls, self.fault, self.wrong_sql = [], fault, wrong_sql
    def set_context(self, index, snapshot):
        self.index, self.snapshot = index, snapshot
    def create(self, **params):
        self.calls.append(params)
        if self.fault:
            raise self.fault
        system = params["messages"][0]["content"]
        if system.startswith("Select relevant"):
            text = json.dumps({"tables": [{"name": "orders", "columns": ["amount"]}]})
        elif system.startswith("Repair"):
            text = "SELECT SUM(amount) FROM orders"
        else:
            text = "SELECT SUM(amount) FROM orders" if not self.wrong_sql else "SELECT absent FROM orders"
        return NS(status_code=200, choices=[NS(message=NS(content=text), finish_reason="stop")])


def test_four_groups_shared_configuration_gold_firewall_and_audit(tmp_path, suite_files):
    clients = {g: ScriptedSDK(wrong_sql=True) for g in "ABCD"}
    folder = tmp_path / "suite"
    summary = run_suite(*suite_files, folder, execution="mock", client_factory=clients.get)
    assert summary["model_calls"] == summary["request_audit"]["prepared_attempts"] == 24
    assert summary["batch_budget"] == 24 and summary["remote_api_requests"] == 0
    for g, expected in zip("ABCD", (3, 6, 6, 9)):
        rows = records(folder, g)
        assert [r["case_id"] for r in rows] == [1, 2, 3]
        assert all(r["experiment_group"] == g and r["model_id"] == "fixture" for r in rows)
        assert sum(r["model_calls"] for r in rows) == len(clients[g].calls) == expected
        assert all(r["repair_attempts"] == (1 if g in "CD" else 0) for r in rows)
        assert summary["group_summaries"][g]["local_result_match_proxy_rate"] == (1 if g in "CD" else 0)
        assert all(r["schema_linking_enabled"] == (g in "BD") and r["repair_enabled"] == (g in "CD") for r in rows)
        audit = json.loads((folder / f"{g}.audit.json").read_text())
        ids = [a["attempt_id"] for a in audit["attempts"]]
        assert ids == [i for r in rows for i in r["request_attempt_ids"]]
        assert all(str(a["case_id"]).startswith(g + ":") for a in audit["attempts"])
        assert "GOLD_ONLY_SUITE_SENTINEL" not in json.dumps(clients[g].calls) + (folder / f"{g}.jsonl").read_text()
    config = json.loads((folder / 'batch.config.json').read_text())
    assert config["common"]["request_retries"] == config["common"]["sdk_retries"] == 0
    assert config["case_ids"] == [1, 2, 3] and not config["api_authorized"]
    assert len(summary["paired_comparisons"]) == 6
    assert summary["paired_comparisons"]["A_vs_C"]["outcome_counts"] == {"right_only_match": 3}
    assert all(r["execution_recovery_candidate"] for r in summary["paired_comparisons"]["A_vs_C"]["cases"])


@pytest.mark.parametrize("batch_cap", [0, 1, 7, 10, 24])
def test_batch_cap_never_exceeded_and_fixed_denominator(tmp_path, suite_files, batch_cap):
    clients = {g: ScriptedSDK(wrong_sql=True) for g in "ABCD"}
    folder = tmp_path / "capped"
    summary = run_suite(*suite_files, folder, execution="mock", batch_budget=batch_cap, client_factory=clients.get)
    assert sum(len(c.calls) for c in clients.values()) == summary["model_calls"] <= batch_cap
    assert summary["request_audit"]["prepared_attempts"] <= batch_cap
    for group in "ABCD":
        rows = records(folder, group)
        assert len(rows) == summary["group_summaries"][group]["planned_cases"] == 3
        assert summary["group_summaries"][group]["local_result_match_proxy_rate"] == sum(r["local_result_match_proxy"] is True for r in rows) / 3


def test_group_cap_does_not_borrow_and_other_groups_continue(tmp_path, suite_files):
    clients = {g: ScriptedSDK(wrong_sql=True) for g in "ABCD"}
    folder = tmp_path / "group_cap"
    summary = run_suite(*suite_files, folder, execution="mock", group_budgets={"A": 1}, client_factory=clients.get)
    assert len(clients["A"].calls) == 1 and len(clients["D"].calls) == 9
    assert summary["group_summaries"]["A"]["planned_cases"] == 3
    assert any(not r["processed"] for r in records(folder, "A"))


@pytest.mark.parametrize("kind", ["auth", "network", "unknown"])
def test_provider_failure_keeps_every_record_and_stops_whole_batch(tmp_path, suite_files, kind):
    req = httpx.Request("POST", "https://offline.invalid")
    fault = {"auth": APIStatusError("fixture secret", response=httpx.Response(401, request=req), body=None),
             "network": APIConnectionError(request=req), "unknown": RuntimeError("secret raw SDK body")}[kind]
    clients = {g: ScriptedSDK(fault=fault) for g in "ABCD"}
    folder = tmp_path / "failed"
    summary = run_suite(*suite_files, folder, execution="mock", client_factory=clients.get)
    assert summary["model_calls"] == sum(len(c.calls) for c in clients.values()) == 1
    assert summary["stop_reason"] and len(records(folder, "D")) == 3
    assert summary["group_summaries"]["A"]["sql_generation_success_rate"] == 0
    assert all(not r["processed"] for r in records(folder, "D"))
    assert "secret raw SDK body" not in (folder / "A.jsonl").read_text()
    if kind == "network":
        assert summary["request_audit"]["indeterminate_attempts"] == 1


def test_dry_run_no_sdk_no_dotenv_and_no_accuracy(tmp_path, suite_files, monkeypatch):
    monkeypatch.setattr(platform, "Model", lambda **kw: pytest.fail("Dry-run constructed SDK"))
    folder = tmp_path / "dry"
    summary = run_suite(*suite_files, folder)
    assert summary["model_calls"] == summary["request_audit"]["prepared_attempts"] == 0
    for group in "ABCD":
        assert all(r["status"] == "dry_run_ok" and not r["final_sql"] for r in records(folder, group))
        assert summary["group_summaries"][group]["local_result_match_proxy_rate"] is None


def test_mock_never_loads_production_env(tmp_path, suite_files, monkeypatch):
    def no_dotenv(*args, **kw):
        import os
        assert os.environ.get("PYTHON_DOTENV_DISABLED") == "1"
    monkeypatch.setattr('bird_starter.model.load_project_env', no_dotenv)
    run_suite(*suite_files, tmp_path / "offline", execution="mock", groups=("A",))


def test_truncated_result_is_unknown_and_kept_in_denominator(tmp_path, suite_files):
    path, cases, db = suite_files
    config = json.loads(path.read_text())
    config["common"]["max_rows"] = 1
    path.write_text(json.dumps(config))
    class ManyRows(ScriptedSDK):
        def create(self, **params):
            self.calls.append(params)
            return NS(status_code=200, choices=[NS(message=NS(content="SELECT amount FROM orders"))])
    folder = tmp_path / "truncated"
    summary = run_suite(path, cases, db, folder, groups=("A",), execution="mock", client_factory=lambda g: ManyRows())
    rows = records(folder, "A")
    assert all(r["final_execution_ok"] and r["local_result_match_proxy"] is None and r["output_truncated"] for r in rows)
    assert summary["group_summaries"]["A"]["local_result_match_proxy_rate"] == 0
    assert summary["group_summaries"]["A"]["proxy_unknown_cases"] == 3


@pytest.mark.parametrize("damage", ["retry", "redirect", "parallel", "prompt", "model", "pricing", "groups", "budget", "data", "manifest", "database"])
def test_configuration_and_data_drift_fail_before_sdk(tmp_path, suite_files, damage):
    path, cases, db = suite_files
    config = json.loads(path.read_text())
    if damage == "retry": config["common"]["request_retries"] = 1
    elif damage == "redirect": config["common"]["http_redirects"] = True
    elif damage == "parallel": config["common"]["concurrency"] = 2
    elif damage == "prompt": config["common"]["prompt_version"] = "wrong"
    elif damage == "model": config["common"]["model"] = ""
    elif damage == "pricing": config["pricing"]["input_per_million"] = 1
    elif damage == "groups": config["groups"]["A"]["repair"] = True
    elif damage == "budget": config["groups"]["D"]["max_http_attempts"] = 10
    elif damage == "data": config["dataset_sha256"] = "wrong"
    elif damage == "manifest": config["manifest_sha256"] = "wrong"
    else: config["database_sha256"]["demo"] = "wrong"
    path.write_text(json.dumps(config))
    folder = tmp_path / "invalid"
    with pytest.raises(ValueError):
        run_suite(path, cases, db, folder, execution="mock", client_factory=lambda g: pytest.fail("SDK created"))
    assert not folder.exists()


@pytest.mark.parametrize("kwargs", [{"groups": ("E",)}, {"groups": ("A", "A")}, {"batch_budget": -1},
    {"batch_budget": 25}, {"group_budgets": {"A": 4}}, {"execution": "live"}])
def test_invalid_requests_and_missing_authorization(tmp_path, suite_files, kwargs):
    with pytest.raises(ValueError):
        run_suite(*suite_files, tmp_path / "rejected", **kwargs)


def test_duplicate_output_refused_without_sdk(tmp_path, suite_files):
    folder = tmp_path / "once"
    run_suite(*suite_files, folder, groups=("A",))
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    with pytest.raises(FileExistsError):
        run_suite(*suite_files, folder, execution="mock", client_factory=lambda g: pytest.fail("Replayed"))
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}


def test_prepared_fsync_failure_blocks_mock_sdk(tmp_path, monkeypatch):
    import bird_starter.request_audit as audit
    batch = RequestLog(tmp_path / "batch.attempts.jsonl", "fixture-run", "fixture", 24)
    scoped = GroupRequestLog(batch, "D", 9)
    calls = []
    model = Model(model="fixture", max_retries=0, max_calls=9, attempt_log=scoped,
                  client=NS(chat=NS(completions=NS(create=lambda **kw: calls.append(kw)))))
    model.set_case_id(1)
    monkeypatch.setattr(audit.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fixture")))
    try:
        with pytest.raises(ModelFailure, match="model_audit_error"):
            model.ask("fixture", "fixture")
        assert not calls and batch.reserved == scoped.reserved == 1
    finally:
        batch.close()


def test_unknown_usage_partial_totals_and_paired_unknown(tmp_path, suite_files):
    folder = tmp_path / "reports"
    run_suite(*suite_files, folder, execution="mock", groups=("A", "B"))
    a, b = records(folder, "A"), records(folder, "B")
    report = summarize_group(a, [1, 2, 3])
    assert report["usage_missing_calls"] == 3 and report["prompt_tokens"] is None and report["estimated_cost"] is None
    a[0]["local_result_match_proxy"] = True
    b[0]["local_result_match_proxy"] = None
    paired = paired_comparisons({"A": a, "B": b})["A_vs_B"]
    assert paired["cases"][0]["outcome"] == "unassessable"
    with pytest.raises(ValueError): summarize_group(a[:2], [1, 2, 3])
    with pytest.raises(ValueError): paired_comparisons({"A": a, "B": list(reversed(b))})
    b[0]["measurement_kind"] = "REAL_MODEL"
    with pytest.raises(ValueError): paired_comparisons({"A": a, "B": b})


@pytest.mark.parametrize("point", ["during_repair", "after_response"])
def test_hard_exit_batch_not_replayed(tmp_path, suite_files, point):
    folder = tmp_path / "crash"
    script = '''
import os,sys,socket
from pathlib import Path
from types import SimpleNamespace as NS
from bird_starter import config
import bird_starter.ablation as platform
config.PROJECT_ROOT=Path(sys.argv[1]).parent
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline'))
socket.socket.connect_ex=socket.socket.connect
class SDK:
 count=0
 def create(self,**kw):
  self.count+=1
  if self.count==2 and sys.argv[5]=='during_repair': os._exit(41)
  return NS(status_code=200,choices=[NS(message=NS(content='SELECT absent FROM orders' if self.count==1 else 'SELECT 1'))])
if sys.argv[5]=='after_response':
 platform.OfflineEvaluator.evaluate=lambda *a,**kw: os._exit(42)
platform.run_suite(*sys.argv[1:5],groups=('C',),execution='mock',client_factory=lambda g:SDK())
'''
    process = subprocess.run([sys.executable, "-c", script, *(str(p) for p in suite_files), str(folder), point],
                             capture_output=True, timeout=30)
    assert process.returncode == (41 if point == "during_repair" else 42), process.stderr
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    report = inspect_experiment(folder / "batch.config.json")
    assert report["prepared_attempts"] == report["budget_reserved"] == 2
    assert report["indeterminate_attempts"] == (1 if point == "during_repair" else 0)
    assert report["automatic_requests_available"] == 0
    with pytest.raises(FileExistsError): run_suite(*suite_files, folder)
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}


def test_cli_group_selection_and_authorization(tmp_path, suite_files):
    config, cases, root = suite_files
    out = tmp_path / "cli"
    assert main(["--config", str(config), "--cases", str(cases), "--db-root", str(root), "--out-dir", str(out),
                 "--groups", "B", "--mock-llm", "--group-budget", "B=2"]) == 1
    assert (out / "B.jsonl").exists() and not (out / "A.jsonl").exists()
    for flags in (["--live"], ["--mock-llm", "--allow-api"], ["--group-budget", "A=1", "--group-budget", "A=2"]):
        with pytest.raises(SystemExit): main(["--out-dir", str(tmp_path / "no"), *flags])


def test_security_rejections_never_turn_into_repair_requests(tmp_path, suite_files):
    class Dangerous(ScriptedSDK):
        def create(self, **params):
            if params["messages"][0]["content"].startswith("Select relevant"):
                return super().create(**params)
            assert not params["messages"][0]["content"].startswith("Repair")
            self.calls.append(params)
            return NS(status_code=200, choices=[NS(message=NS(content="DROP TABLE orders"))])
    clients = {g: Dangerous() for g in "ABCD"}
    folder = tmp_path / "danger"
    summary = run_suite(*suite_files, folder, execution="mock", client_factory=clients.get)
    assert summary["model_calls"] == 18 and summary["sqlite_sha256_unchanged"]
    for g in "ABCD":
        assert all(not r["final_execution_ok"] and r["repair_attempts"] == 0 for r in records(folder, g))


@pytest.mark.parametrize("content", ["invalid JSON", '{"tables":[{"name":"absent","columns":["x"]}]}', None])
def test_schema_fallback_reported_without_hiding_selector_failures(tmp_path, suite_files, content):
    class InvalidLink(ScriptedSDK):
        def create(self, **params):
            if params["messages"][0]["content"].startswith("Select relevant"):
                self.calls.append(params)
                return NS(status_code=200, choices=[NS(message=NS(content=content))])
            return super().create(**params)
    folder = tmp_path / "fallback"
    summary = run_suite(*suite_files, folder, groups=("B",), execution="mock", client_factory=lambda g: InvalidLink())
    assert summary["group_summaries"]["B"]["schema_valid_selection_rate"] == 0
    assert summary["model_calls"] == 6
    assert all(r["schema_fallback"] and r["final_execution_ok"] for r in records(folder, "B"))


def test_partial_usage_never_presented_as_complete_totals(tmp_path, suite_files):
    class Partial(ScriptedSDK):
        def create(self, **params):
            response = super().create(**params)
            if self.index == 0:
                response.usage = NS(prompt_tokens=12, completion_tokens=3)
            return response
    summary = run_suite(*suite_files, tmp_path / "usage", groups=("A",), execution="mock", client_factory=lambda g: Partial())
    report = summary["group_summaries"]["A"]
    assert report["usage_missing_calls"] == 2 and report["prompt_tokens"] is None
    assert report["known_prompt_tokens_subtotal"] == 12 and report["known_completion_tokens_subtotal"] == 3
    assert report["estimated_cost"] is None


def test_configured_model_wins_over_shell_aliases(tmp_path, suite_files, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "not-the-experiment-model")
    monkeypatch.setenv("DEEPSEEK_MODEL", "also-not-the-experiment-model")
    sdk = ScriptedSDK()
    summary = run_suite(*suite_files, tmp_path / "model", groups=("A",), execution="mock", client_factory=lambda g: sdk)
    assert summary["model_calls"] == 3 and all(call["model"] == "fixture" for call in sdk.calls)


def test_cross_process_directory_reservation(tmp_path, suite_files):
    folder = tmp_path / "reserved"
    script = '''
import sys,socket
from bird_starter.ablation import run_suite
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline'))
socket.socket.connect_ex=socket.socket.connect
try:
 run_suite(*sys.argv[1:5],groups=('A',))
except FileExistsError:
 sys.exit(17)
'''
    command = [sys.executable, "-c", script, *(str(p) for p in suite_files), str(folder)]
    children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for child in children:
        _, error = child.communicate(timeout=30)
        assert child.returncode in {0, 17}, error
    assert sorted(p.returncode for p in children) == [0, 17]
    assert len(records(folder, "A")) == 3
    assert inspect_experiment(folder / "batch.config.json")["prepared_attempts"] == 0


def test_latency_excludes_evaluator_snapshot_cache_preparation(tmp_path, suite_files, monkeypatch):
    clock = [0.0]
    read = platform.read_schema
    def slow_metadata(*args):
        clock[0] += 100
        return read(*args)
    monkeypatch.setattr(platform, "read_schema", slow_metadata)
    monkeypatch.setattr(platform.time, "monotonic", lambda: clock[0])
    folder = tmp_path / "timing"
    run_suite(*suite_files, folder, execution="mock", groups=("A", "B"))
    assert all(r["total_latency_ms"] == 0 for g in "AB" for r in records(folder, g))
