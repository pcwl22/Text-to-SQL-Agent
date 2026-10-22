"""Offline response fixtures only; no production .env, HTTP or benchmark results."""
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from bird_starter import model as adapter
from bird_starter.baseline import run_baseline
from bird_starter.data import AgentInput
from bird_starter.model import Model, ModelFailure, RESPONSE_PARSER_VERSION
from bird_starter.request_audit import RequestLog, inspect_request_log


def envelope(content="SELECT 1", finish="stop", reasoning_tokens=None, **message_fields):
    return {"model": "offline-response-model", "status_code": 200, "_request_id": "offline-request-id",
            "choices": [{"message": {"content": content, **message_fields}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 1024,
                      "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}}}


@pytest.fixture
def factory(tmp_path):
    logs = []
    def make(result, cap=10):
        path = tmp_path / f"fixture-{len(logs)}.attempts.jsonl"
        log = RequestLog(path, "offline-run", "offline-model", cap)
        logs.append(log)
        calls = []
        def create(**kwargs):
            events = [json.loads(line) for line in path.read_text().splitlines()]
            assert events[-1]["event"] == "ATTEMPT_PREPARED"
            calls.append(kwargs)
            return result
        model = Model(model="offline-model", max_retries=3, max_calls=cap, attempt_log=log,
                      client=NS(chat=NS(completions=NS(create=create))),
                      sleeper=lambda _: pytest.fail("A parsing failure must never retry"))
        model.set_case_id(7)
        return model, path, calls
    yield make
    for log in logs:
        log.close()


@pytest.mark.parametrize("content", [" SELECT 1 ", [{"type": "text", "text": "SELECT "},
                                                     {"type": "output_text", "text": "1"}],
                                    [NS(type="text", text="SELECT 1")]])
def test_visible_text_and_nonstandard_blocks(factory, content):
    model, path, calls = factory(envelope(content))
    assert model.ask("fixture system", "fixture question") == "SELECT 1"
    assert len(calls) == 1
    item = inspect_request_log(path, "offline-run", 10)["attempts"][0]
    assert item["result_status"] == "SUCCEEDED"
    assert item["http_status"] == 200 and item["provider_request_id"] == "offline-request-id"
    details = item["response_metadata"]
    assert details["choices_count"] == 1 and details["finish_reason"] == "stop"
    assert details["response_model"] == "offline-response-model"
    assert details["parser_version"] == RESPONSE_PARSER_VERSION
    assert details["visible_content_chars"] == 8 and details["reasoning_tokens"] is None
    # 1024 completion tokens == the configured cap is not proof of truncation.
    assert model.last_call["completion_tokens"] == model.max_output_tokens == 1024


@pytest.mark.parametrize("result,category", [
    (envelope(None), "empty_response"),
    (envelope(""), "empty_response"),
    (envelope(" \n "), "empty_response"),
    (envelope([]), "empty_response"),
    (envelope(None, "length"), "model_output_truncated"),
    (envelope("SELECT", "length"), "model_output_truncated"),
    (envelope(None, reasoning_tokens=1024), "model_reasoning_only"),
    (envelope(None, reasoning_content="PRIVATE_REASONING_FIXTURE"), "model_reasoning_only"),
    (envelope(None, "length", reasoning_tokens=1024), "model_output_truncated"),
    ({"choices": []}, "model_empty_choices"),
    ({"choices": None}, "model_invalid_response"),
    ({"choices": "invalid"}, "model_invalid_response"),
    ({}, "model_invalid_response"),
    (None, "model_invalid_response"),
    ({"choices": [None]}, "model_invalid_response"),
    ({"choices": [{"message": {}}]}, "model_invalid_response"),
    (envelope(123), "model_unsupported_content"),
    (envelope({"text": "SELECT 1"}), "model_unsupported_content"),
    (envelope([{"type": "reasoning", "text": "PRIVATE_REASONING_FIXTURE"}]), "model_unsupported_content"),
    (envelope([{"type": ["text"], "text": "SELECT 1"}]), "model_unsupported_content"),
    (envelope([{"type": "text", "text": 123}]), "model_unsupported_content"),
    (envelope([{"type": "text", "text": "SELECT 1"}, {"type": "image_url"}]), "model_unsupported_content"),
])
def test_bad_response_classification_never_retries(factory, result, category):
    model, path, calls = factory(result)
    with pytest.raises(ModelFailure) as exc:
        model.ask("PRIVATE_PROMPT_FIXTURE", "PRIVATE_QUESTION_FIXTURE")
    assert exc.value.category == category
    assert len(calls) == model.total_calls == model.last_call["model_calls"] == 1
    report = inspect_request_log(path, "offline-run", 10)
    item = report["attempts"][0]
    assert item["result_status"] == "FAILED" and item["error_category"] == category
    assert report["budget_reserved"] == 1 and report["automatic_requests_available"] == 0
    assert report["audit_state"] == "VALID"
    text = path.read_text()
    for private in ("PRIVATE_REASONING_FIXTURE", "PRIVATE_PROMPT_FIXTURE", "PRIVATE_QUESTION_FIXTURE"):
        assert private not in text


def test_standard_sdk_object_and_reasoning_metadata(factory):
    result = NS(model="offline-response-model", status_code=200,
                choices=[NS(message=NS(content=None), finish_reason="stop")],
                usage=NS(prompt_tokens=12, completion_tokens=1024,
                         completion_tokens_details=NS(reasoning_tokens=1024)))
    model, path, calls = factory(result)
    with pytest.raises(ModelFailure, match="model_reasoning_only"):
        model.ask("fixture", "fixture")
    details = inspect_request_log(path, "offline-run", 10)["attempts"][0]["response_metadata"]
    assert details["reasoning_tokens"] == 1024 and details["content_shape"] == "null"
    assert details["visible_content_chars"] == 0 and len(calls) == 1


@pytest.mark.parametrize("body", [b'not valid JSON', b'{}', b'{"choices": []}',
                                  b'{"choices": [{"message": null}]}'])
def test_installed_sdk_invalid_http_200_is_failed_once(tmp_path, monkeypatch, body):
    calls = []
    def transport(request):
        calls.append(1)
        return httpx.Response(200, headers={"content-type": "application/json", "x-request-id": "offline-id"},
                              content=body)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-not-a-real-key")
    monkeypatch.setattr(adapter, "DefaultHttpxClient", lambda **kw:
                        httpx.Client(transport=httpx.MockTransport(transport), **kw))
    path = tmp_path / "sdk.attempts.jsonl"
    log = RequestLog(path, "offline-run", "offline-model", 10)
    model = Model(model="offline-model", base_url="https://example.invalid/v1", max_retries=3,
                  attempt_log=log, sleeper=lambda _: pytest.fail("No retry permitted"))
    model.set_case_id(7)
    try:
        with pytest.raises(ModelFailure) as exc:
            model.ask("fixture", "fixture")
        assert exc.value.category in {"model_invalid_response", "model_empty_choices"}
        assert model.client.max_retries == 0 and model.client._client.follow_redirects is False
        assert len(calls) == model.total_calls == 1
        event = inspect_request_log(path, "offline-run", 10)["attempts"][0]
        assert event["result_status"] == "FAILED" and event["http_status"] == 200
        assert event["provider_request_id"] == "offline-id"
    finally:
        model.close()
        log.close()


def test_parsing_failures_use_all_ten_reservations_without_implicit_replay(factory):
    model, path, calls = factory(envelope(None))
    # Ten explicit offline calls, not automatic retries or authorization to resume.
    for n in range(10):
        model.set_case_id(n)
        with pytest.raises(ModelFailure, match="empty_response"):
            model.ask("fixture", "fixture")
    with pytest.raises(ModelFailure, match="model_budget_exceeded"):
        model.ask("fixture", "fixture")
    assert len(calls) == model.total_calls == 10
    report = inspect_request_log(path, "offline-run", 10)
    assert report["budget_reserved"] == 10 and report["automatic_requests_available"] == 0
    assert all(a["result_status"] == "FAILED" for a in report["attempts"])


@pytest.mark.parametrize("content,finish,reasoning_tokens,category", [
    ("SELECT 1", "stop", 0, None),
    ([{"type": "text", "text": "SELECT 1"}], "stop", None, None),
    (None, "stop", 1024, "model_reasoning_only"),
    ("SELECT", "length", 900, "model_output_truncated"),
])
def test_installed_sdk_preserves_response_diagnostics(tmp_path, monkeypatch, content, finish,
                                                    reasoning_tokens, category):
    calls = []
    def transport(request):
        calls.append(1)
        body = envelope(content, finish, reasoning_tokens)
        body.update(id="offline-id", object="chat.completion", created=1)
        body["choices"][0].update(index=0)
        body["choices"][0]["message"]["role"] = "assistant"
        return httpx.Response(200, json=body, headers={"x-request-id": "offline-id"})
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-not-a-real-key")
    monkeypatch.setattr(adapter, "DefaultHttpxClient", lambda **kw:
                        httpx.Client(transport=httpx.MockTransport(transport), **kw))
    path = tmp_path / "sdk.attempts.jsonl"
    log = RequestLog(path, "offline-run", "offline-model", 10)
    model = Model(model="offline-model", base_url="https://example.invalid/v1", max_retries=3,
                  attempt_log=log, sleeper=lambda _: pytest.fail("No retry permitted"))
    model.set_case_id(7)
    try:
        if category:
            with pytest.raises(ModelFailure, match=category):
                model.ask("fixture", "fixture")
        else:
            assert model.ask("fixture", "fixture") == "SELECT 1"
        assert len(calls) == 1 and model.client.max_retries == 0
        event = inspect_request_log(path, "offline-run", 10)["attempts"][0]
        assert event["error_category"] == category and event["http_status"] == 200
        assert event["provider_request_id"] == "offline-id"
        assert event["response_metadata"]["finish_reason"] == finish
        assert event["response_metadata"]["reasoning_tokens"] == reasoning_tokens
        assert event["response_metadata"]["response_model"] == "offline-response-model"
    finally:
        model.close()
        log.close()


def test_truncated_sql_never_reaches_sql_execution(factory, tiny_db):
    model, _, calls = factory(envelope("SELECT 1", "length"))
    agent = AgentInput.from_case({"question": "fixture", "evidence": ""}, tiny_db[1], "baseline")
    before = tiny_db[1].read_bytes()
    result = run_baseline(agent, model)
    assert result["error_type"] == "model_output_truncated" and len(calls) == 1
    assert result["generated_sql"] == "" and result["execution_time_ms"] is None
    assert tiny_db[1].read_bytes() == before


def test_metadata_whitelist_and_old_log_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-secret")
    path = tmp_path / "metadata.attempts.jsonl"
    log = RequestLog(path, "offline-run", "offline-model", 2)
    try:
        ident = log.prepare(1)
        log.finish(ident, "SUCCEEDED", response_metadata={
            "finish_reason": "Bearer fixture-secret", "response_model": "offline-fixture-secret",
            "reasoning_tokens": -1, "choices_count": True, "visible_content_chars": 8,
            "prompt_tokens_details": {"cached_tokens": True, "audio_tokens": -1, "extra": "PRIVATE_BODY"},
            "completion_tokens_details": {"reasoning_tokens": "PRIVATE_BODY", "audio_tokens": 2,
                                           "accepted_prediction_tokens": 3, "rejected_prediction_tokens": None,
                                           "Authorization": "PRIVATE_HEADER"},
            "content": "PRIVATE_BODY", "Authorization": "PRIVATE_HEADER", "prompt": "PRIVATE_PROMPT"})
        ident = log.prepare(2)
        log.finish(ident, "SUCCEEDED")  # Original version-1 record without new fields.
        report = inspect_request_log(path, "offline-run", 2)
        assert report["audit_state"] == "VALID" and report["budget_reserved"] == 2
        details = report["attempts"][0]["response_metadata"]
        assert details["reasoning_tokens"] is None and details["choices_count"] is None
        assert details["response_model"] == "[REDACTED]"
        assert details["prompt_tokens_details"] == {"cached_tokens": None, "audio_tokens": None}
        assert details["completion_tokens_details"] == {"reasoning_tokens": None, "audio_tokens": 2,
                                                        "accepted_prediction_tokens": 3,
                                                        "rejected_prediction_tokens": None}
        assert "response_metadata" not in report["attempts"][1]
        text = path.read_text()
        for private in ("offline-fixture-secret", "fixture-secret", "PRIVATE_BODY", "PRIVATE_HEADER", "PRIVATE_PROMPT"):
            assert private not in text
    finally:
        log.close()


@pytest.mark.parametrize("response,category", [
    (envelope(None), "empty_response"),
    ({"choices": []}, "model_empty_choices"),
    ({"choices": [None]}, "model_invalid_response"),
    (envelope([{"type": "image_url"}]), "model_unsupported_content"),
])
def test_invalid_response_never_reaches_executor(factory, tiny_db, monkeypatch, response, category):
    from bird_starter import baseline
    monkeypatch.setattr(baseline, "execute_select", lambda *a, **kw: pytest.fail("Invalid response reached SQLite"))
    model, _, calls = factory(response)
    agent = AgentInput.from_case({"question": "fixture", "evidence": ""}, tiny_db[1], "baseline")
    result = baseline.run_baseline(agent, model)
    assert result["error_type"] == category and len(calls) == 1
    assert result["generated_sql"] == "" and result["execution_time_ms"] is None
