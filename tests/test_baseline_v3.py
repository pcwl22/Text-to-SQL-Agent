"""V3 preparation: synthetic SQLite + MockTransport/SDK only, never live BIRD inference."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
from openai import OpenAI
import pytest

import run_eval
from bird_starter import request_audit as audit
from bird_starter.model import Model


IDS = [1471, 1472, 1473, 1476, 1479, 1480, 1481, 1482, 1483, 1484]
MODEL = "deepseek-4.1-flash"


def fixture_args(tmp_path, tiny_db):
    cases = tmp_path / "fixture-cases.json"
    cases.write_text(json.dumps([
        {"question_id": case_id, "db_id": "demo", "question": f"fixture question {case_id}",
         "evidence": "fixture clue", "SQL": "EVALUATOR_ONLY_FIXTURE"} for case_id in IDS
    ]), encoding="utf-8")
    out = tmp_path / "independent-v3" / "baseline_10_v3.jsonl"
    args = ["--mode", "baseline", "--limit", "10", "--cases", str(cases),
            "--db-root", str(tiny_db[0]), "--model", MODEL,
            "--base-url", "https://gw.lunaroute.com/v1", "--request-retries", "0",
            "--max-model-calls", "10", "--request-timeout", "300", "--max-output-tokens", "2048",
            "--token-parameter", "max_completion_tokens", "--case-error-policy", "isolate",
            "--experiment-version", "phase2-v3-flash-2048", "--allow-api", "--no-proxy", "--out", str(out)]
    return args, out


def setup_sdk(monkeypatch, scenario="normal"):
    calls = []
    def transport(request):
        params = json.loads(request.content)
        assert params["model"] == MODEL
        assert params["n"] == 1 and params["max_completion_tokens"] == 2048
        assert "max_tokens" not in params
        assert "EVALUATOR_ONLY_FIXTURE" not in str(params)
        assert len(params["messages"]) == 2
        index = len(calls)
        assert f"fixture question {IDS[index]}" in params["messages"][1]["content"]
        # No failure message or previous generated SQL is carried into the next question.
        assert all(f"fixture question {n}" not in str(params) for n in IDS if n != IDS[index])
        calls.append(params)
        if index == 6:
            if scenario == "timeout":
                raise httpx.ReadTimeout("offline fixture", request=request)
            status = {"auth": 401, "missing_model": 404, "bad_config": 400,
                      "rate_limit": 429, "server_error": 503}.get(scenario)
            if status:
                return httpx.Response(status, json={"error": {"message": "offline fixture"}},
                                      headers={"x-request-id": f"fixture-{index}"})
            if scenario == "invalid":
                return httpx.Response(200, json={"choices": None})
        text, finish = "SELECT SUM(amount) FROM orders", "stop"
        if index == 6:
            if scenario == "empty":
                text = None
            elif scenario == "length":
                text, finish = "SELECT", "length"
            elif scenario == "syntax":
                text = "SELECT ( FROM"
            elif scenario == "sql_execution":
                text = "SELECT abs(-9223372036854775808)"
        return httpx.Response(200, headers={"x-request-id": f"fixture-{index}"}, json={
            "id": f"fixture-{index}", "object": "chat.completion", "created": 1, "model": MODEL,
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50,
                      "prompt_tokens_details": {"cached_tokens": 5, "audio_tokens": 0},
                      "completion_tokens_details": {"reasoning_tokens": 0, "audio_tokens": 0,
                                                    "accepted_prediction_tokens": 2,
                                                    "rejected_prediction_tokens": 1}}})
    def factory(**kwargs):
        client = OpenAI(api_key="offline-fixture-key", base_url=kwargs["base_url"], max_retries=0,
                        timeout=kwargs["request_timeout"],
                        http_client=httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=False))
        assert client.max_retries == 0 and not client._client.follow_redirects
        return Model(**kwargs, client=client, sleeper=lambda _: pytest.fail("No retries authorized"))
    monkeypatch.setattr(run_eval, "Model", factory)
    return calls


@pytest.mark.parametrize("scenario,processed,requests,error", [
    ("normal", 10, 10, None),
    ("empty", 10, 10, "empty_response"),
    ("length", 10, 10, "model_output_truncated"),
    ("syntax", 10, 10, "sql_syntax_error"),
    ("sql_execution", 10, 10, "sql_execution_error"),
    ("auth", 7, 7, "model_authentication_error"),
    ("missing_model", 7, 7, "model_request_error"),
    ("bad_config", 7, 7, "model_request_error"),
    ("rate_limit", 7, 7, "model_rate_limit"),
    ("server_error", 7, 7, "model_server_error"),
    ("invalid", 7, 7, "model_invalid_response"),
    ("timeout", 7, 7, "model_timeout"),
])
def test_ten_case_mock_experiment(tmp_path, tiny_db, monkeypatch, scenario, processed, requests, error):
    args, out = fixture_args(tmp_path, tiny_db)
    calls = setup_sdk(monkeypatch, scenario)
    before = hashlib.sha256(tiny_db[1].read_bytes()).hexdigest()
    summary = run_eval.main(args)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    config = json.loads(out.with_suffix(".config.json").read_text())
    persisted = json.loads(out.with_suffix(".summary.json").read_text())
    report = audit.inspect_experiment(out.with_suffix(".config.json"))
    assert summary == persisted
    assert [row["case_id"] for row in rows] == IDS[:processed]
    assert len(calls) == summary["model_calls"] == requests <= 10
    assert report["prepared_attempts"] == report["budget_reserved"] == requests
    assert len({a["attempt_id"] for a in report["attempts"]}) == requests
    assert report["automatic_requests_available"] == 0
    assert config["case_error_policy"] == "isolate" and config["request_retries"] == config["sdk_retries"] == 0
    assert config["max_output_tokens"] == 2048 and config["token_parameter"] == "max_completion_tokens"
    assert config["concurrency"] == 1 and config["experiment_version"] == "phase2-v3-flash-2048"
    assert config["case_ids"] == IDS and len(config["run_id"]) == 32
    assert config["model"] == MODEL
    assert config["run_id"] not in {"017d25c18ccf4316a4a3984eabe1c840", "23aebb9647134310b593e8646f321bb4"}
    for row, attempt in zip(rows, report["attempts"]):
        assert row["run_id"] == config["run_id"] == report["run_id"]
        assert row["case_id"] == attempt["case_id"]
        assert row["request_attempt_ids"] == [attempt["attempt_id"]]
        assert row["model_calls"] == 1
        if row["usage_reported_calls"]:
            assert (row["prompt_tokens"], row["completion_tokens"]) == (20, 30)
            details = attempt["response_metadata"]
            assert details["prompt_tokens_details"] == {"cached_tokens": 5, "audio_tokens": 0}
            assert details["completion_tokens_details"]["accepted_prediction_tokens"] == 2
    if error:
        row = rows[6]
        assert row["error_type"] == error
        isolated = error in run_eval.ISOLATED_CASE_ERRORS
        assert row["failure_scope"] == ("case" if isolated else "system")
        assert summary["stop_reason"] == (None if isolated else error)
        assert summary["generation_successes"] == (9 if isolated and scenario in {"empty", "length"}
                                                     else processed if isolated else 6)
        assert summary["execution_successes"] == (9 if isolated else 6)
        if isolated:
            assert all(r["execution_ok"] and r["error_type"] is None for r in rows[7:])
            assert all(r["failure_scope"] is None for r in rows[7:])
        if scenario in {"empty", "length", "invalid", "auth"}:
            assert row["generated_sql"] == "" and row["execution_time_ms"] is None
    else:
        assert summary["execution_successes"] == 10 and summary["stop_reason"] is None
    assert summary["unprocessed_case_ids"] == IDS[processed:]
    assert hashlib.sha256(tiny_db[1].read_bytes()).hexdigest() == before
    assert "EVALUATOR_ONLY_FIXTURE" not in "".join(p.read_text() for p in out.parent.iterdir())


@pytest.mark.parametrize("stage,expected_requests", [("prepare", 6), ("result", 7)])
def test_audit_failure_stops_batch(tmp_path, tiny_db, monkeypatch, stage, expected_requests):
    args, out = fixture_args(tmp_path, tiny_db)
    calls = setup_sdk(monkeypatch)
    original = audit.RequestLog._append
    def append(log, event):
        target = "ATTEMPT_PREPARED" if stage == "prepare" else "ATTEMPT_RESULT"
        if event["event"] == target and event.get("case_id") == IDS[6]:
            log.poisoned = True
            raise audit.AuditError()
        original(log, event)
    monkeypatch.setattr(audit.RequestLog, "_append", append)
    summary = run_eval.main(args)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert summary["stop_reason"] == "model_audit_error" and len(rows) == 7
    assert len(calls) == summary["model_calls"] == expected_requests
    assert rows[-1]["failure_scope"] == "system" and rows[-1]["generated_sql"] == ""
    assert rows[-1]["execution_time_ms"] is None
    if stage == "result":
        assert summary["request_audit"]["indeterminate_attempts"] == 1


def test_budget_exhaustion_does_not_send_next_request(tmp_path, tiny_db, monkeypatch):
    args, out = fixture_args(tmp_path, tiny_db)
    args[args.index("--max-model-calls") + 1] = "6"
    calls = setup_sdk(monkeypatch)
    summary = run_eval.main(args)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(calls) == summary["model_calls"] == summary["request_audit"]["prepared_attempts"] == 6
    assert summary["stop_reason"] == "model_budget_exceeded" and len(rows) == 7
    assert rows[-1]["model_calls"] == 0 and rows[-1]["request_attempt_ids"] == []
    assert rows[-1]["failure_scope"] == "system"


@pytest.mark.parametrize("change", ["retry", "graph"])
def test_isolation_rejects_retries_and_graph(tmp_path, tiny_db, change):
    args, out = fixture_args(tmp_path, tiny_db)
    args[args.index("--request-retries" if change == "retry" else "--mode") + 1] = "1" if change == "retry" else "graph"
    with pytest.raises(SystemExit) as exc:
        run_eval.main(args)
    assert exc.value.code == 2 and not out.parent.exists()


def test_default_policy_keeps_empty_response_batch_stop(tmp_path, tiny_db, monkeypatch):
    args, _ = fixture_args(tmp_path, tiny_db)
    index = args.index("--case-error-policy")
    del args[index:index + 2]
    calls = setup_sdk(monkeypatch, "empty")
    summary = run_eval.main(args)
    assert summary["case_error_policy"] == "legacy" and summary["stop_reason"] == "empty_response"
    assert len(calls) == summary["records"] == 7


@pytest.mark.parametrize("category", ["data_error", "internal_error", "model_error", "model_reasoning_only",
                                    "model_unsupported_content", "model_empty_choices", "unknown_system_exception"])
def test_isolation_unknown_categories_fail_closed(category):
    assert run_eval._stop_for_error(category, "isolate")


@pytest.mark.parametrize("prefix", ["baseline_10", "baseline_10_v2"])
def test_historical_prefixes_cannot_be_overwritten(tmp_path, tiny_db, monkeypatch, prefix):
    args, _ = fixture_args(tmp_path, tiny_db)
    root = tmp_path / "historical"
    root.mkdir()
    out = root / f"{prefix}.jsonl"
    originals = {}
    for suffix in (".jsonl", ".config.json", ".summary.json", ".attempts.jsonl"):
        path = out.with_suffix(suffix)
        path.write_bytes(b"historical fixture, preserve exactly")
        originals[path] = path.read_bytes()
    args[args.index("--out") + 1] = str(out)
    monkeypatch.setattr(run_eval, "Model", lambda **kw: pytest.fail("Must not construct SDK"))
    with pytest.raises(SystemExit) as exc:
        run_eval.main(args)
    assert exc.value.code == 2 and all(p.read_bytes() == data for p, data in originals.items())


def test_same_experiment_race_has_one_owner_across_processes(tmp_path, tiny_db):
    args, out = fixture_args(tmp_path, tiny_db)
    sync = tmp_path / "sync"
    sync.mkdir()
    script = '''
import json, os, socket, sys, time
from pathlib import Path
from types import SimpleNamespace as NS
import run_eval
from bird_starter import config
from bird_starter.model import Model
sync=Path(sys.argv[1])
config.PROJECT_ROOT=sync
socket.socket.connect=lambda *a,**kw: (_ for _ in ()).throw(AssertionError("offline only"))
socket.socket.connect_ex=socket.socket.connect
write=run_eval.write_new_json
def reserve(path, value):
    if str(path).endswith(".config.json"):
        (sync / f"ready-{os.getpid()}").touch()
        until=time.monotonic()+15
        while not (sync / "release").exists():
            if time.monotonic()>until: raise RuntimeError("barrier timeout")
            time.sleep(.02)
    return write(path,value)
run_eval.write_new_json=reserve
def factory(**kwargs):
    with (sync / "constructed").open("a") as f: f.write("one\\n")
    def create(**params):
        with (sync / "calls").open("a") as f: f.write("one\\n")
        return NS(choices=[NS(message=NS(content="SELECT 1"),finish_reason="stop")])
    return Model(**kwargs,client=NS(chat=NS(completions=NS(create=create))))
run_eval.Model=factory
run_eval.main(sys.argv[2:])
'''
    env = {**os.environ, "PYTHON_DOTENV_DISABLED": "1"}
    processes = [subprocess.Popen([sys.executable, "-c", script, str(sync), *args],
                                 cwd=Path(run_eval.__file__).parent, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        until = time.monotonic() + 15
        while len(list(sync.glob("ready-*"))) < 2 and time.monotonic() < until:
            time.sleep(.02)
        assert len(list(sync.glob("ready-*"))) == 2
        (sync / "release").touch()
        outputs = [p.communicate(timeout=20) for p in processes]
        assert sorted(p.returncode for p in processes) == [0, 2], outputs
        assert len((sync / "constructed").read_text().splitlines()) == 1
        assert len((sync / "calls").read_text().splitlines()) == 10
        summary = json.loads(out.with_suffix(".summary.json").read_text())
        assert summary["records"] == summary["model_calls"] == 10
        assert summary["request_audit"]["prepared_attempts"] == 10
    finally:
        for p in processes:
            if p.poll() is None:
                p.kill()  # Only these test-owned mock subprocesses.
                p.communicate(timeout=5)
