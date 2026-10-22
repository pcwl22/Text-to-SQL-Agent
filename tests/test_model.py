from types import SimpleNamespace as NS

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, APIStatusError, AuthenticationError

from bird_starter.model import Model, ModelFailure, TrackedModel, redact
from bird_starter import model as model_module
from bird_starter.request_audit import RequestLog


def response(text="SELECT 1", usage=True):
    return NS(choices=[NS(message=NS(content=text))], model="fixture-response-model",
              usage=NS(prompt_tokens=20, completion_tokens=5) if usage else None)


def client_with(events):
    calls, events = [], iter(events)
    def create(**kwargs):
        calls.append(kwargs)
        result = next(events)
        if isinstance(result, Exception):
            raise result
        return result
    return NS(chat=NS(completions=NS(create=create))), calls


@pytest.fixture
def model_factory(tmp_path):
    logs = []
    def factory(**kwargs):
        log = RequestLog(tmp_path / f"model-{len(logs)}.attempts.jsonl", "fixture-run",
                         kwargs.get("model", "fixture"), kwargs.get("max_calls", 10))
        logs.append(log)
        model = Model(**kwargs, attempt_log=log)
        model.set_case_id(1)
        return model
    yield factory
    for log in logs:
        log.close()


def test_model_parameters_usage_and_one_candidate(model_factory):
    client, calls = client_with([response()])
    model = model_factory(model="fixture", client=client, max_retries=0)
    assert model.ask("system", "user") == "SELECT 1"
    assert calls[0]["n"] == 1 and calls[0]["temperature"] == 0
    assert calls[0]["max_completion_tokens"] == 1024
    assert model.last_call["model_calls"] == 1
    assert model.last_call["prompt_tokens"] == 20
    assert model.last_call["completion_tokens"] == 5
    assert model.configuration()["sdk_retries"] == 0


def test_http_retry_is_bounded_and_identical(model_factory):
    request = httpx.Request("POST", "https://example.invalid")
    error = APIStatusError("fixture", response=httpx.Response(503, request=request), body={})
    client, calls = client_with([error, response()])
    model = model_factory(model="fixture", client=client, max_calls=2, sleeper=lambda _: None)
    tracked = TrackedModel(model)
    assert tracked.ask("system", "user") == "SELECT 1"
    assert calls[0] == calls[1]
    assert tracked.metrics["model_calls"] == 2 and tracked.metrics["usage_reported_calls"] == 1
    with pytest.raises(ModelFailure, match="budget"):
        tracked.ask("system", "user")
    assert len(calls) == 2


def test_authentication_failure_has_no_retry_or_secret(monkeypatch, model_factory):
    request = httpx.Request("POST", "https://example.invalid")
    key = "TEST_ONLY_INVALID_CREDENTIAL"  # Synthetic Mock redaction fixture, never a provider key.
    monkeypatch.setenv("OPENAI_API_KEY", key)
    exc = AuthenticationError(key, response=httpx.Response(401, request=request), body={"secret": key})
    client, calls = client_with([exc, response()])
    model = model_factory(model="fixture", client=client, sleeper=lambda _: None)
    with pytest.raises(ModelFailure) as error:
        model.ask("system", "user")
    assert error.value.category == "model_authentication_error"
    assert key not in str(error.value) and len(calls) == 1
    assert key not in str(redact({"error": key, "nested": ["Bearer abcdefgh"]}))


def test_timeout_is_not_retried_even_with_configured_budget(model_factory):
    request = httpx.Request("POST", "https://example.invalid")
    client, calls = client_with([APITimeoutError(request=request)] * 3)
    model = model_factory(model="fixture", client=client, max_retries=1, sleeper=lambda _: None)
    with pytest.raises(ModelFailure, match="model_timeout"):
        model.ask("system", "user")
    assert len(calls) == 1 and model.last_call["model_calls"] == 1


def test_empty_response_and_usage_not_invented(model_factory):
    client, calls = client_with([response("  ", usage=False)])
    model = model_factory(model="fixture", client=client)
    with pytest.raises(ModelFailure, match="empty_response"):
        model.ask("system", "user")
    assert len(calls) == 1 and model.last_call["prompt_tokens"] is None


def test_omit_temperature_and_compatible_token_parameter(model_factory):
    client, calls = client_with([response()])
    model = model_factory(model="fixture", client=client, temperature=None, token_parameter="max_tokens")
    model.ask("system", "user")
    assert "temperature" not in calls[0] and "max_completion_tokens" not in calls[0]
    assert calls[0]["max_tokens"] == 1024


@pytest.mark.parametrize("kwargs", [{"base_url": "https://user:pass@example.invalid/v1"},
                                    {"base_url": "https://example.invalid?key=secret"},
                                    {"request_timeout": 0}, {"max_retries": 4},
                                    {"temperature": float("nan")}])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        Model(model="fixture", client=NS(), **kwargs)


def test_sdk_timeout_and_retries_are_explicit(monkeypatch, tmp_path):
    client, calls = client_with([response()])
    constructed = []
    def factory(**kwargs):
        constructed.append(kwargs)
        return client
    monkeypatch.setattr(model_module, "OpenAI", factory)
    log = RequestLog(tmp_path / "sdk.attempts.jsonl", "fixture-run", "fixture", 10)
    model = Model(model="fixture", base_url="https://example.invalid/v1", request_timeout=2.5,
                  attempt_log=log)
    model.set_case_id(1)
    model.ask("system", "user")
    assert constructed[0]["timeout"] == 2.5
    assert constructed[0]["max_retries"] == 0
    assert constructed[0]["http_client"].follow_redirects is False
    constructed[0]["http_client"].close()
    log.close()
    assert "api_key" not in constructed[0] and "api_key" not in calls[0]


@pytest.mark.parametrize("status,category", [(401, "model_authentication_error"),
                                           (404, "model_request_error"),
                                           (429, "model_rate_limit"),
                                           (500, "model_server_error"),
                                           (307, "model_request_error")])
def test_real_sdk_mock_http_failures_count_and_never_retry_or_redirect(monkeypatch, tmp_path, status, category):
    """Actual installed SDK, in-memory HTTP transport; no remote API requests."""
    attempts = []
    def transport(request):
        attempts.append(request)
        return httpx.Response(status, headers={"location": "https://example.invalid/redirect"},
                              json={"error": {"message": "offline fixture", "type": "fixture"}})
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-key")
    def http_client(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(model_module, "DefaultHttpxClient", http_client)
    log = RequestLog(tmp_path / "sdk.attempts.jsonl", "fixture-run", "fixture", 1)
    model = Model(model="fixture", base_url="https://example.invalid/v1", max_retries=0, max_calls=1,
                  attempt_log=log)
    model.set_case_id(1)
    tracked = TrackedModel(model)
    try:
        assert model.client.max_retries == 0 and model.client._client.follow_redirects is False
        with pytest.raises(ModelFailure, match=category):
            tracked.ask("fixture system", "fixture question")
        assert len(attempts) == tracked.metrics["model_calls"] == model.total_calls == 1
        assert model.last_call["model_calls"] == 1
        with pytest.raises(ModelFailure, match="budget"):
            tracked.ask("fixture system", "fixture question")
        assert len(attempts) == 1 and tracked.metrics["model_calls"] == 1
    finally:
        model.close()
        log.close()
