"""Offline hard-exit and provider failures in the new repair branch."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import httpx
from openai import APIConnectionError, APITimeoutError, APIStatusError
import pytest

import run_eval
from bird_starter.model import Model
from bird_starter.request_audit import inspect_experiment


def fixture_cases(path, count):
    path.write_text(json.dumps([{"question_id": n, "db_id": "demo", "question": "fixture", "evidence": "",
                                 "SQL": "GOLD_RECOVERY_SENTINEL"} for n in range(count)]))


def arguments(cases, root, out, cap=10):
    return ["--mode", "repair_only", "--cases", str(cases), "--db-root", str(root), "--out", str(out),
            "--model", "fixture", "--request-retries", "0", "--max-model-calls", str(cap),
            "--allow-api", "--no-proxy"]


@pytest.mark.parametrize("phase", ["generation", "repair"])
@pytest.mark.parametrize("kind", ["auth", "network", "timeout", "unknown", "empty", "length"])
def test_batch_system_failure_stop_no_retry(tmp_path, tiny_db, monkeypatch, phase, kind):
    cases = tmp_path / "cases.json"
    fixture_cases(cases, 2)
    calls = []
    request = httpx.Request("POST", "https://offline.invalid/v1")
    failure = {
        "auth": lambda: APIStatusError("fixture", response=httpx.Response(401, request=request), body=None),
        "network": lambda: APIConnectionError(request=request),
        "timeout": lambda: APITimeoutError(request=request),
        "unknown": lambda: RuntimeError("secret raw failure must not leave SDK"),
    }
    failed_index = 1 if phase == "repair" else 0
    def create(**kwargs):
        index = len(calls)
        calls.append(kwargs)
        if index == failed_index:
            if kind in failure:
                raise failure[kind]()
            return NS(status_code=200, choices=[NS(message=NS(content=""), finish_reason="length" if kind == "length" else "stop")])
        return NS(status_code=200, choices=[NS(message=NS(content="SELECT missing FROM orders" if index == 0 else "SELECT 1"),
                                                   finish_reason="stop")])
    monkeypatch.setattr(run_eval, "Model", lambda **kw: Model(**kw, client=NS(chat=NS(completions=NS(create=create)))))
    out = tmp_path / "run.jsonl"
    summary = run_eval.main(arguments(cases, tiny_db[0], out))
    if kind in {"empty", "length"}:
        assert summary["records"] == 2 and not summary["stop_reason"]
        assert len(calls) == failed_index + 2  # Next case gets exactly its own generation request.
    else:
        assert summary["records"] == 1 and summary["stopped_early"] and summary["stop_reason"]
        assert len(calls) == failed_index + 1
    assert summary["request_audit"]["prepared_attempts"] == len(calls)
    assert "secret raw failure" not in out.read_text()


def test_ten_http_cap_shared_between_generation_and_repair(tmp_path, tiny_db, monkeypatch):
    cases = tmp_path / "cases.json"
    fixture_cases(cases, 6)
    calls = []
    def create(**kwargs):
        sql = "SELECT missing FROM orders" if len(calls) % 2 == 0 else "SELECT 1"
        calls.append(kwargs)
        return NS(status_code=200, choices=[NS(message=NS(content=sql))])
    monkeypatch.setattr(run_eval, "Model", lambda **kw: Model(**kw, client=NS(chat=NS(completions=NS(create=create)))))
    out = tmp_path / "cap.jsonl"
    summary = run_eval.main(arguments(cases, tiny_db[0], out))
    assert summary["stop_reason"] == "model_budget_exceeded"
    assert summary["request_audit"]["prepared_attempts"] == summary["model_calls"] == len(calls) == 10
    assert summary["sql_repair"]["repair_model_calls"] == 5
    assert json.loads(out.with_suffix('.config.json').read_text())["sdk_retries"] == 0


@pytest.mark.parametrize("when", ["during_repair", "after_response_before_jsonl"])
def test_hard_exit_repair_has_no_automatic_replay(tmp_path, tiny_db, when):
    cases = tmp_path / "cases.json"
    fixture_cases(cases, 1)
    out = tmp_path / "crashed.jsonl"
    script = '''
import os,sys,socket
from pathlib import Path
from types import SimpleNamespace as NS
import run_eval
from bird_starter import config
from bird_starter.model import Model
config.PROJECT_ROOT=Path(sys.argv[1]).parent
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline'))
socket.socket.connect_ex=socket.socket.connect
count=0
def create(**kw):
 global count
 count+=1
 if count==2 and sys.argv[4]=='during_repair': os._exit(37)
 return NS(status_code=200,choices=[NS(message=NS(content='SELECT absent FROM orders' if count==1 else 'SELECT 1'))])
run_eval.Model=lambda **kw: Model(**kw,client=NS(chat=NS(completions=NS(create=create))))
record=run_eval.schema_graph_record
def crash_after_graph(output):
 if sys.argv[4]=='after_response_before_jsonl': os._exit(38)
 return record(output)
run_eval.schema_graph_record=crash_after_graph
run_eval.main(['--mode','repair_only','--cases',sys.argv[1],'--db-root',sys.argv[2],'--out',sys.argv[3],
 '--model','fixture','--request-retries','0','--max-model-calls','10','--allow-api','--no-proxy'])
'''
    result = subprocess.run([sys.executable, "-c", script, str(cases), str(tiny_db[0]), str(out), when],
                            capture_output=True, timeout=30)
    assert result.returncode == (37 if when == "during_repair" else 38), result.stderr
    config = out.with_suffix('.config.json')
    report = inspect_experiment(config)
    assert report["prepared_attempts"] == report["budget_reserved"] == 2
    assert report["indeterminate_attempts"] == (1 if when == "during_repair" else 0)
    assert report["automatic_requests_available"] == 0 and not report["automatic_resume_allowed"]
    assert out.stat().st_size == 0 and not out.with_suffix('.summary.json').exists()
    before = {p.name: p.read_bytes() for p in tmp_path.glob('crashed.*')}
    with pytest.raises(SystemExit):
        run_eval.main(arguments(cases, tiny_db[0], out))
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob('crashed.*')}


def test_repair_cli_disabled_by_default_and_legacy_limit_not_reused(tmp_path, tiny_db, monkeypatch):
    cases = tmp_path / "cases.json"
    fixture_cases(cases, 1)
    def create(**kw):
        return NS(status_code=200, choices=[NS(message=NS(content="SELECT missing FROM orders"))])
    monkeypatch.setattr(run_eval, "Model", lambda **kw: Model(**kw, client=NS(chat=NS(completions=NS(create=create)))))
    out = tmp_path / "disabled.jsonl"
    args = arguments(cases, tiny_db[0], out)
    args[1] = "schema_only"
    args += ["--schema-linking", "off", "--max-repairs", "100"]
    result = run_eval.main(args)
    assert result["model_calls"] == 1 and result["sql_repair"]["repair_attempt_count"] == 0
    assert json.loads(out.with_suffix('.config.json').read_text())["sql_repair"] is False


@pytest.mark.parametrize("mode", ["repair_only", "full"])
def test_evaluator_wrong_initial_answer_cannot_trigger_repair(tmp_path, tiny_db, monkeypatch, mode):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"question_id": 7, "db_id": "demo", "question": "fixture sum", "evidence": "",
                                 "SQL": "SELECT SUM(amount) FROM orders"}]))
    calls = []
    def create(**kw):
        calls.append(kw)
        return NS(status_code=200, choices=[NS(message=NS(content="SELECT 999"))])
    monkeypatch.setattr(run_eval, "Model", lambda **kw: Model(**kw, client=NS(chat=NS(completions=NS(create=create)))))
    out = tmp_path / "wrong_answer.jsonl"
    args = arguments(cases, tiny_db[0], out)
    args[1] = mode
    args.remove("--no-proxy")
    args += ["--schema-linking", "off"]
    result = run_eval.main(args)
    row = json.loads(out.read_text())
    assert row["local_result_match_proxy"] is False and row["execution_ok"]
    assert result["model_calls"] == len(calls) == 1
    assert row["repair_attempts"] == 0 and row["repair_success"] is None
    assert "SELECT SUM(amount) FROM orders" not in json.dumps(calls)


def test_explicit_repair_off_overrides_full_mode(tmp_path, tiny_db, monkeypatch):
    cases = tmp_path / "cases.json"
    fixture_cases(cases, 1)
    calls = []
    def create(**kw):
        calls.append(kw)
        return NS(status_code=200, choices=[NS(message=NS(content="SELECT missing FROM orders"))])
    monkeypatch.setattr(run_eval, "Model", lambda **kw: Model(**kw, client=NS(chat=NS(completions=NS(create=create)))))
    out = tmp_path / "off.jsonl"
    args = arguments(cases, tiny_db[0], out)
    args[1] = "full"
    result = run_eval.main(args + ["--schema-linking", "off", "--sql-repair", "off"])
    config = json.loads(out.with_suffix('.config.json').read_text())
    assert config["schema_linking"] is False and config["sql_repair"] is False and config["max_repairs"] == 0
    assert result["model_calls"] == len(calls) == 1
