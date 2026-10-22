"""Offline fake SDKs and hard-exit subprocesses; never contact a model provider."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import httpx
from openai import APIConnectionError, APITimeoutError, APIStatusError
import pytest

from bird_starter import request_audit as audit
from bird_starter import model as model_module
from bird_starter.model import Model, ModelFailure
import run_eval


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def reply():
    return NS(status_code=200, _request_id="fixture-provider-id", model="fixture",
              usage=NS(prompt_tokens=12, completion_tokens=3),
              choices=[NS(message=NS(content="SELECT 1"))])


def make_model(tmp_path, create, *, max_calls=10, max_retries=0):
    path = tmp_path / "fixture.attempts.jsonl"
    log = audit.RequestLog(path, "fixture-run", "fixture", max_calls)
    model = Model(model="fixture", client=NS(chat=NS(completions=NS(create=create))),
                  attempt_log=log, max_calls=max_calls, max_retries=max_retries, sleeper=lambda _: None)
    model.set_case_id(42)
    return path, log, model


def test_prepared_is_fsynced_before_sdk_and_result_has_metadata(tmp_path, monkeypatch):
    synced = []
    real_fsync = audit.os.fsync
    def fsync(fd):
        real_fsync(fd)
        synced.append(fd)
    monkeypatch.setattr(audit.os, "fsync", fsync)
    def create(**kwargs):
        events = lines(path)
        assert [e["event"] for e in events] == ["RUN_CREATED", "ATTEMPT_PREPARED"]
        assert len(synced) == 2  # Header and PREPARED persisted before SDK entry.
        assert "SQL" not in kwargs and set(kwargs["messages"][1]) == {"role", "content"}
        return reply()
    path, log, model = make_model(tmp_path, create)
    try:
        assert model.ask("fixture system", "fixture question") == "SELECT 1"
        prepared, result = lines(path)[1:]
        assert prepared["attempt_id"] == result["attempt_id"]
        assert result["run_id"] == "fixture-run" and result["case_id"] == 42
        assert result["attempt_sequence"] == 1 and result["model_id"] == "fixture"
        assert result["result_status"] == "SUCCEEDED" and result["http_status"] == 200
        assert result["provider_request_id"] == "fixture-provider-id"
        assert (result["prompt_tokens"], result["completion_tokens"]) == (12, 3)
        assert prepared["prepared_at_utc"] and result["response_completed_at_utc"]
        assert len(synced) == 3
    finally:
        log.close()


@pytest.mark.parametrize("failure", ["write", "flush", "fsync"])
def test_prepared_persistence_failure_never_calls_sdk(tmp_path, monkeypatch, failure):
    calls = []
    path, log, model = make_model(tmp_path, lambda **kwargs: calls.append(kwargs))
    class BrokenStream:
        closed = False
        def write(self, value):
            if failure == "write":
                raise OSError("fixture storage error")
            return log.stream_original.write(value)
        def flush(self):
            if failure == "flush":
                raise OSError("fixture storage error")
            log.stream_original.flush()
        def fileno(self):
            return log.stream_original.fileno()
    log.stream_original = log.stream
    log.stream = BrokenStream()
    if failure == "fsync":
        monkeypatch.setattr(audit.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fixture")))
    try:
        with pytest.raises(ModelFailure, match="model_audit_error"):
            model.ask("fixture", "fixture")
        assert not calls and model.total_calls == 0
        assert log.reserved == 1 and log.poisoned
        with pytest.raises(ModelFailure):
            model.ask("fixture", "fixture")
        assert not calls
    finally:
        log.stream_original.close()


@pytest.mark.parametrize("exception", [APIConnectionError, APITimeoutError])
def test_network_exception_is_indeterminate_and_never_retried(tmp_path, exception):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise exception(request=httpx.Request("POST", "https://example.invalid"))
    path, log, model = make_model(tmp_path, create, max_retries=3)
    try:
        with pytest.raises(ModelFailure):
            model.ask("fixture", "fixture")
        assert len(calls) == model.total_calls == 1
        assert lines(path)[-1]["result_status"] == "INDETERMINATE"
        assert lines(path)[-1]["response_completed_at_utc"] is None
        recovered = audit.inspect_request_log(path, "fixture-run", 10)
        assert recovered["budget_reserved"] == recovered["indeterminate_attempts"] == 1
        assert recovered["automatic_requests_available"] == 0
        with pytest.raises(ModelFailure, match="model_audit_error"):
            model.ask("fixture", "fixture")
        assert len(calls) == 1
    finally:
        log.close()


def test_interrupt_during_sdk_is_not_a_success_or_retry(tmp_path):
    def create(**kwargs):
        raise KeyboardInterrupt()
    path, log, model = make_model(tmp_path, create)
    try:
        with pytest.raises(KeyboardInterrupt):
            model.ask("fixture", "fixture")
        assert lines(path)[-1]["error_category"] == "model_interrupted"
        assert audit.inspect_request_log(path, "fixture-run", 10)["indeterminate_attempts"] == 1
    finally:
        log.close()


def test_hard_exit_leaves_durable_prepared_and_recovery_cannot_replay(tmp_path):
    path = tmp_path / "hard.attempts.jsonl"
    script = '''
import os, sys, socket
from pathlib import Path
from types import SimpleNamespace as NS
from bird_starter import config
from bird_starter.request_audit import RequestLog
from bird_starter.model import Model
config.PROJECT_ROOT = Path(sys.argv[1]).parent
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("offline"))
socket.socket.connect_ex = socket.socket.connect
def create(**kwargs):
    os._exit(23)
log = RequestLog(sys.argv[1], "fixture-run", "fixture", 10)
model = Model(model="fixture", client=NS(chat=NS(completions=NS(create=create))), attempt_log=log)
model.set_case_id(42)
model.ask("fixture", "fixture")
'''
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, timeout=20)
    assert result.returncode == 23
    before = path.read_bytes()
    report = audit.inspect_request_log(path, "fixture-run", 10)
    assert report["indeterminate_attempts"] == report["prepared_attempts"] == report["budget_reserved"] == 1
    assert report["automatic_requests_available"] == 0
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        audit.RequestLog(path, "fixture-run", "fixture", 10)
    assert path.read_bytes() == before


def test_result_persistence_failure_stops_after_one_sdk_call(tmp_path, monkeypatch):
    calls = []
    path, log, model = make_model(tmp_path, lambda **kwargs: calls.append(kwargs) or reply())
    append = log._append
    def fail_result(event):
        if event["event"] == "ATTEMPT_RESULT":
            log.poisoned = True
            raise audit.AuditError()
        append(event)
    monkeypatch.setattr(log, "_append", fail_result)
    try:
        with pytest.raises(ModelFailure, match="model_audit_error"):
            model.ask("fixture", "fixture")
        assert len(calls) == model.total_calls == 1
        assert audit.inspect_request_log(path, "fixture-run", 10)["indeterminate_attempts"] == 1
    finally:
        log.close()


def test_prepared_does_not_prove_sdk_was_called(tmp_path, monkeypatch):
    calls = []
    path, log, model = make_model(tmp_path, lambda **kwargs: calls.append(kwargs) or reply())
    prepare = log.prepare
    def interrupt(case_id):
        prepare(case_id)
        raise KeyboardInterrupt()
    monkeypatch.setattr(log, "prepare", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            model.ask("fixture", "fixture")
        assert not calls and model.total_calls == 0
        report = audit.inspect_request_log(path, "fixture-run", 10)
        assert report["indeterminate_attempts"] == report["budget_reserved"] == 1
        assert report["actual_http_attempts"] == "UNKNOWN"
    finally:
        log.close()


def test_result_fsync_failure_forbids_further_requests(tmp_path, monkeypatch):
    calls = []
    path, log, model = make_model(tmp_path, lambda **kwargs: calls.append(kwargs) or reply())
    fsync = audit.os.fsync
    def fail_after_prepare(fd):
        if len(calls):
            raise OSError("fixture result fsync failure")
        fsync(fd)
    monkeypatch.setattr(audit.os, "fsync", fail_after_prepare)
    try:
        with pytest.raises(ModelFailure, match="model_audit_error"):
            model.ask("fixture", "fixture")
        with pytest.raises(ModelFailure, match="model_audit_error"):
            model.ask("fixture", "fixture")
        assert len(calls) == model.total_calls == log.reserved == 1
    finally:
        log.close()


def test_missing_log_never_refunds_a_new_run_budget(tmp_path):
    config = tmp_path / "run.config.json"
    config.write_text(json.dumps({"run_id": "fixture-run", "max_model_calls": 10,
                                 "request_audit_version": 1, "attempt_log_file": "run.attempts.jsonl"}))
    report = audit.inspect_experiment(config)
    assert report["audit_state"] == "INDETERMINATE" and report["budget_reserved"] == 10
    assert report["automatic_requests_available"] == 0


def test_log_context_must_match_model_and_budget(tmp_path):
    log = audit.RequestLog(tmp_path / "fixture.attempts.jsonl", "fixture-run", "fixture", 10)
    try:
        with pytest.raises(ValueError, match="model/budget"):
            Model(model="other", max_calls=10, client=object(), attempt_log=log)
        with pytest.raises(ValueError, match="model/budget"):
            Model(model="fixture", max_calls=11, client=object(), attempt_log=log)
        assert log.reserved == 0
    finally:
        log.close()


def test_default_sdk_cannot_be_called_without_request_log(monkeypatch):
    calls, clients = [], []
    def factory(**kwargs):
        clients.append(kwargs["http_client"])
        return NS(chat=NS(completions=NS(create=lambda **params: calls.append(params))))
    monkeypatch.setattr(model_module, "OpenAI", factory)
    model = Model(model="fixture", base_url="https://example.invalid/v1")
    try:
        with pytest.raises(ModelFailure, match="model_audit_required"):
            model.ask("fixture", "fixture")
        assert not calls and model.total_calls == 0
    finally:
        for client in clients:
            client.close()


def test_injected_sdk_also_requires_log_and_case_context(tmp_path):
    calls = []
    client = NS(chat=NS(completions=NS(create=lambda **kwargs: calls.append(kwargs))))
    model = Model(model="fixture", client=client)
    with pytest.raises(ModelFailure, match="model_audit_required"):
        model.ask("fixture", "fixture")
    log = audit.RequestLog(tmp_path / "context.attempts.jsonl", "fixture-run", "fixture", 10)
    try:
        model = Model(model="fixture", client=client, attempt_log=log)
        with pytest.raises(ModelFailure, match="model_audit_context_missing"):
            model.ask("fixture", "fixture")
        assert not calls and log.reserved == 0
    finally:
        log.close()


def test_actual_sdk_raw_success_preserves_http_and_usage_metadata(tmp_path, monkeypatch):
    path = tmp_path / "raw.attempts.jsonl"
    log = audit.RequestLog(path, "fixture-run", "fixture", 1)
    def transport(request):
        assert lines(path)[-1]["event"] == "ATTEMPT_PREPARED"
        return httpx.Response(200, headers={"x-request-id": "fixture-http-request"}, json={
            "id": "fixture-completion", "object": "chat.completion", "created": 1, "model": "fixture",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "SELECT 1"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}})
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-key")
    monkeypatch.setattr(model_module, "DefaultHttpxClient", lambda **kwargs:
                        httpx.Client(transport=httpx.MockTransport(transport), **kwargs))
    model = Model(model="fixture", base_url="https://example.invalid/v1", max_calls=1,
                  max_retries=0, attempt_log=log)
    model.set_case_id(42)
    try:
        assert model.ask("fixture", "fixture") == "SELECT 1"
        result = lines(path)[-1]
        assert result["http_status"] == 200 and result["provider_request_id"] == "fixture-http-request"
        assert result["prompt_tokens"] == 12 and result["completion_tokens"] == 3
        assert result["result_status"] == "SUCCEEDED"
        assert model.client.max_retries == 0 and not model.client._client.follow_redirects
    finally:
        model.close()
        log.close()


def test_ten_failed_http_responses_exhaust_budget(tmp_path):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise APIStatusError("fixture", response=httpx.Response(400, request=httpx.Request(
            "POST", "https://example.invalid"), headers={"x-request-id": "fixture-failure"}), body={})
    path, log, model = make_model(tmp_path, create)
    try:
        for _ in range(10):
            with pytest.raises(ModelFailure, match="model_request_error"):
                model.ask("fixture", "fixture")
        with pytest.raises(ModelFailure, match="model_budget_exceeded"):
            model.ask("fixture", "fixture")
        assert len(calls) == model.total_calls == log.reserved == 10
        report = audit.inspect_request_log(path, "fixture-run", 10)
        assert report["http_responses_recorded"] == report["budget_reserved"] == 10
        assert {a["attempt_sequence"] for a in report["attempts"]} == set(range(1, 11))
        assert len({a["attempt_id"] for a in report["attempts"]}) == 10
    finally:
        log.close()


@pytest.mark.parametrize("damage", ["partial", "bad_identity", "duplicate_result", "bad_json"])
def test_damaged_logs_block_full_budget_without_repairing_original(tmp_path, damage):
    path, log, model = make_model(tmp_path, lambda **kwargs: reply())
    model.ask("fixture", "fixture")
    log.close()
    data = path.read_text(encoding="utf-8")
    if damage == "partial":
        data += '{"event": "ATTEMPT_PREPARED"'
    elif damage == "bad_identity":
        data = data.replace('"run_id": "fixture-run"', '"run_id": "other"', 1)
    elif damage == "duplicate_result":
        data += data.splitlines()[-1] + "\n"
    else:
        data = 'invalid fixture\n'
    path.write_text(data, encoding="utf-8")
    before = path.read_bytes()
    report = audit.inspect_request_log(path, "fixture-run", 10)
    assert report["audit_state"] == "INDETERMINATE" and report["budget_reserved"] == 10
    assert report["automatic_requests_available"] == 0 and path.read_bytes() == before


def test_audit_excludes_prompts_gold_headers_and_secrets(tmp_path, monkeypatch):
    secret = "fixture-private-provider-key"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    def create(**kwargs):
        result = reply()
        result._request_id = secret
        return result
    path, log, model = make_model(tmp_path, create)
    try:
        model.ask("fixture prompt marker", "fixture question marker")
        text = path.read_text(encoding="utf-8")
        assert all(value not in text for value in [secret, "fixture prompt marker", "fixture question marker",
                                                   "Authorization", "messages", "SELECT 1"])
        assert lines(path)[-1]["provider_request_id"] == "[REDACTED]"
    finally:
        log.close()


def test_historical_run_is_unknown_and_files_are_unchanged(tmp_path):
    config = tmp_path / "old.config.json"
    original = {"run_id": "017d25c18ccf4316a4a3984eabe1c840", "max_model_calls": 10}
    config.write_text(json.dumps(original), encoding="utf-8")
    out = tmp_path / "old.jsonl"
    out.touch()
    report = audit.inspect_experiment(config)
    assert report["historical_http_attempts"] == "UNKNOWN"
    assert report["budget_reserved"] == 10 and report["automatic_requests_available"] == 0
    assert json.loads(config.read_text()) == original and out.read_bytes() == b""
    assert not (tmp_path / "old.attempts.jsonl").exists()


def test_response_survives_interruption_before_case_jsonl(tmp_path, tiny_db, monkeypatch):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"question_id": 42, "db_id": "demo", "question": "fixture question",
                                 "evidence": "", "SQL": "FIXTURE_GOLD_SENTINEL"}]), encoding="utf-8")
    created = []
    def factory(**kwargs):
        client = NS(chat=NS(completions=NS(create=lambda **params: created.append(params) or reply())))
        return Model(**kwargs, client=client)
    monkeypatch.setattr(run_eval, "Model", factory)
    baseline = run_eval.run_baseline
    def interrupt(*args, **kwargs):
        result = baseline(*args, **kwargs)
        assert result["execution_ok"]
        raise KeyboardInterrupt()
    monkeypatch.setattr(run_eval, "run_baseline", interrupt)
    out = tmp_path / "run.jsonl"
    args = ["--mode", "baseline", "--cases", str(cases), "--db-root", str(tiny_db[0]),
            "--model", "fixture", "--out", str(out), "--allow-api"]
    with pytest.raises(KeyboardInterrupt):
        run_eval.main(args)
    assert out.read_bytes() == b"" and not out.with_suffix(".summary.json").exists()
    report = audit.inspect_experiment(out.with_suffix(".config.json"))
    assert report["budget_reserved"] == 1 and report["indeterminate_attempts"] == 0
    assert report["attempts"][0]["result_status"] == "SUCCEEDED"
    assert "FIXTURE_GOLD_SENTINEL" not in out.with_suffix(".attempts.jsonl").read_text()
    with pytest.raises(SystemExit) as error:
        run_eval.main(args)
    assert error.value.code == 2 and len(created) == 1
