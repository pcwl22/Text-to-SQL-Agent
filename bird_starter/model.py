"""Small OpenAI-compatible text generation adapter."""
from __future__ import annotations
import os
import json
import re
import math
import time
from urllib.parse import urlsplit
from openai import (OpenAI, DefaultHttpxClient, APIConnectionError, APITimeoutError,
                    APIStatusError, APIResponseValidationError)

from .config import load_project_env
from .request_audit import AuditError, safe_text, TOKEN_DETAIL_FIELDS

RESPONSE_PARSER_VERSION = "chat-content-v2"


def _field(value, name, default=None):
    """Accept SDK objects and third-party dictionary envelopes, without coercion."""
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _response_text(result, details):
    """Only visible text is a candidate; never promote reasoning/tool output to SQL."""
    choices = _field(result, "choices")
    if not isinstance(choices, list):
        raise ModelFailure("model_invalid_response")
    details["choices_count"] = len(choices)
    if not choices:
        raise ModelFailure("model_empty_choices")
    choice = choices[0]  # n=1; never select an alternative candidate.
    finish_reason = _field(choice, "finish_reason")
    details["finish_reason"] = finish_reason if isinstance(finish_reason, str) else None
    message = _field(choice, "message")
    missing = object()
    content = _field(message, "content", missing)
    if content is missing:
        raise ModelFailure("model_invalid_response")
    reasoning = _field(message, "reasoning_content")
    details["reasoning_content_present"] = isinstance(reasoning, str) and bool(reasoning.strip())
    answer = ""
    unsupported = False
    if content is None:
        details["content_shape"] = "null"
    elif isinstance(content, str):
        details["content_shape"] = "text"
        answer = content
    elif isinstance(content, list):
        details["content_shape"] = "text_blocks"
        texts = []
        for block in content:
            text = _field(block, "text")
            if _field(block, "type") not in ("text", "output_text") or not isinstance(text, str):
                unsupported = True
                break
            texts.append(text)
        answer = "".join(texts) if not unsupported else ""
    else:
        details["content_shape"] = "unsupported"
        unsupported = True
    details["visible_content_chars"] = len(answer.strip()) if not unsupported else None
    # Explicit length is evidence of an incomplete completion, even with some text.
    # The token count alone must never trigger this classification.
    if finish_reason == "length":
        raise ModelFailure("model_output_truncated")
    if unsupported:
        raise ModelFailure("model_unsupported_content")
    if not answer.strip():
        if (details.get("reasoning_tokens") or 0) > 0 or details["reasoning_content_present"]:
            raise ModelFailure("model_reasoning_only")
        raise ModelFailure("empty_response")
    return answer.strip()


class ModelFailure(RuntimeError):
    def __init__(self, category: str):
        self.category = category
        # Never propagate provider bodies/headers/URLs: they can contain credentials.
        super().__init__(category)


def safe_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Base URL must be HTTP(S), without credentials, query or fragment")
    return value.rstrip("/")


def redact(value):
    """Scrub the environment key and common bearer/key patterns before persistence."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            value = value.replace(key, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[\w.\-]+", "Bearer [REDACTED]", value)
        return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
    return value


def _failure(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, (json.JSONDecodeError, APIResponseValidationError)):
        return "model_invalid_response", False
    if isinstance(exc, APITimeoutError):
        return "model_timeout", True
    if isinstance(exc, APIConnectionError):
        return "model_network_error", True
    if isinstance(exc, APIStatusError):
        code = exc.status_code
        if code in {401, 403}:
            return "model_authentication_error", False
        if code == 429:
            return "model_rate_limit", True
        return ("model_server_error", True) if code >= 500 else ("model_request_error", False)
    return "model_error", False

class Model:
    def __init__(self, *, model=None, base_url=None, temperature=0.0, request_timeout=30.0,
                 max_retries=1, max_calls=10, max_output_tokens=1024,
                 token_parameter="max_completion_tokens", client=None, sleeper=time.sleep,
                 attempt_log=None):
        load_project_env()
        self.model = model or os.environ.get("OPENAI_MODEL")
        if not self.model:
            raise ModelFailure("model_configuration_error")
        if (not math.isfinite(request_timeout) or request_timeout <= 0
                or type(max_retries) is not int or not 0 <= max_retries <= 3
                or type(max_calls) is not int or max_calls < 0
                or type(max_output_tokens) is not int or max_output_tokens <= 0
                or (temperature is not None and (not math.isfinite(temperature) or not 0 <= temperature <= 2))
                or token_parameter not in {"max_tokens", "max_completion_tokens"}):
            raise ValueError("Invalid model generation or budget parameters")
        self.base_url = safe_base_url(base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
        self.temperature, self.request_timeout = temperature, request_timeout
        self.max_retries, self.max_calls = max_retries, max_calls
        self.max_output_tokens, self.token_parameter = max_output_tokens, token_parameter
        self.total_calls, self.last_call, self._sleep = 0, {}, sleeper
        self.attempt_log, self.case_id = attempt_log, None
        if attempt_log is not None and (attempt_log.model_id != safe_text(self.model)
                                        or attempt_log.max_attempts != max_calls):
            raise ValueError("Request log model/budget must match the model configuration")
        try:
            # SDK retries disabled so every HTTP attempt is counted against the budget.
            self.client = client if client is not None else OpenAI(
                base_url=self.base_url, timeout=request_timeout, max_retries=0,
                http_client=DefaultHttpxClient(timeout=request_timeout, follow_redirects=False))
        except Exception:
            raise ModelFailure("model_configuration_error") from None

    def configuration(self):
        return {"model": self.model, "base_url": self.base_url, "temperature": self.temperature,
                "request_timeout_seconds": self.request_timeout, "request_retries": self.max_retries,
                "max_model_calls": self.max_calls, "max_output_tokens": self.max_output_tokens,
                "token_parameter": self.token_parameter, "n": 1, "sdk_retries": 0,
                "http_redirects": False, "response_parser_version": RESPONSE_PARSER_VERSION}

    def set_case_id(self, case_id):
        self.case_id = case_id

    def _finish_attempt(self, attempt_id, status, **metadata):
        if self.attempt_log is not None:
            try:
                self.attempt_log.finish(attempt_id, status, **metadata)
            except AuditError:
                raise ModelFailure("model_audit_error") from None

    def ask(self, system: str, user: str) -> str:
        start = time.monotonic()
        self.last_call = {"model_calls": 0, "prompt_tokens": None, "completion_tokens": None,
                          "usage_reported_calls": 0, "response_model": None, "request_attempt_ids": []}
        params = {"model": self.model, "n": 1, self.token_parameter: self.max_output_tokens,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        try:
            if self.attempt_log is None:
                raise ModelFailure("model_audit_required")
            if self.attempt_log is not None and self.case_id is None:
                raise ModelFailure("model_audit_context_missing")
            for attempt in range(self.max_retries + 1):
                if self.total_calls >= self.max_calls:
                    raise ModelFailure("model_budget_exceeded")
                attempt_id = None
                if self.attempt_log is not None:
                    try:
                        attempt_id = self.attempt_log.prepare(self.case_id)
                        self.last_call["request_attempt_ids"].append(attempt_id)
                    except AuditError as exc:
                        raise ModelFailure(exc.category) from None
                self.total_calls += 1
                self.last_call["model_calls"] += 1
                metadata = {"http_status": None, "provider_request_id": None,
                            "prompt_tokens": None, "completion_tokens": None}
                details = {"parser_version": RESPONSE_PARSER_VERSION}
                metadata["response_metadata"] = details
                response_received = False
                try:
                    completions = self.client.chat.completions
                    raw_api = getattr(completions, "with_raw_response", None)
                    if raw_api is not None:
                        raw = raw_api.create(**params)
                        response_received = True
                        metadata.update(http_status=raw.status_code,
                                        provider_request_id=raw.headers.get("x-request-id"))
                        result = raw.parse()
                    else:  # Small injected Mock SDKs need not emulate the raw wrapper.
                        result = completions.create(**params)
                        response_received = True
                        metadata["http_status"] = _field(result, "status_code")
                    metadata["provider_request_id"] = (metadata["provider_request_id"]
                                                       or _field(result, "_request_id"))
                    usage = _field(result, "usage")
                    for key in ("prompt_tokens", "completion_tokens"):
                        value = _field(usage, key)
                        metadata[key] = value if type(value) is int and value >= 0 else None
                        self.last_call[key] = metadata[key]
                    self.last_call["usage_reported_calls"] += int(all(
                        metadata[k] is not None for k in ("prompt_tokens", "completion_tokens")))
                    response_model = _field(result, "model")
                    self.last_call["response_model"] = response_model if isinstance(response_model, str) else None
                    details["response_model"] = self.last_call["response_model"]
                    for name, fields in TOKEN_DETAIL_FIELDS.items():
                        source = _field(usage, name)
                        details[name] = None if source is None else {
                            key: number if type(number := _field(source, key)) is int and number >= 0 else None
                            for key in fields}
                    tokens = _field(_field(usage, "completion_tokens_details"), "reasoning_tokens")
                    details["reasoning_tokens"] = tokens if type(tokens) is int and tokens >= 0 else None
                    answer = _response_text(result, details)
                except Exception as exc:
                    category, retryable = ((exc.category, False) if isinstance(exc, ModelFailure) else _failure(exc))
                    if isinstance(exc, APIStatusError):
                        metadata.update(http_status=exc.status_code,
                                        provider_request_id=getattr(exc, "request_id", None))
                    uncertain = not response_received and metadata["http_status"] is None
                    self._finish_attempt(attempt_id, "INDETERMINATE" if uncertain else "FAILED",
                                         error_category=category, **metadata)
                    # Only an explicit HTTP error response can be eligible for configured retries.
                    # A timeout, unknown outcome or 2xx response parsing failure never grants replay.
                    retryable = retryable and type(metadata["http_status"]) is int and metadata["http_status"] >= 400
                    if retryable and attempt < self.max_retries and self.total_calls < self.max_calls:
                        self._sleep(min(0.25 * 2 ** attempt, 1.0))
                        continue
                    raise ModelFailure(category) from None
                except BaseException:
                    # A hard process termination may leave only PREPARED. Recovery handles both.
                    try:
                        self._finish_attempt(attempt_id, "INDETERMINATE",
                                             error_category="model_interrupted", **metadata)
                    except Exception:
                        pass
                    raise
                self._finish_attempt(attempt_id, "SUCCEEDED", **metadata)
                return answer.strip()
        finally:
            self.last_call["model_latency_ms"] = round((time.monotonic() - start) * 1000, 3)

    def close(self):
        close = getattr(self.client, "close", None)
        if close:
            close()


class TrackedModel:
    """Per-case accounting, including injected offline models and the legacy graph."""
    def __init__(self, model):
        self.delegate = model
        self.reset()

    def reset(self):
        self.metrics = {"model_calls": 0, "model_latency_ms": 0.0, "prompt_tokens": None,
                        "completion_tokens": None, "usage_reported_calls": 0, "response_model": None,
                        "request_attempt_ids": []}

    def ask(self, system, user):
        start = time.monotonic()
        try:
            return self.delegate.ask(system, user)
        finally:
            meta = getattr(self.delegate, "last_call", {})
            self.metrics["model_calls"] += meta.get("model_calls", 1)
            self.metrics["model_latency_ms"] += meta.get("model_latency_ms", (time.monotonic() - start) * 1000)
            self.metrics["usage_reported_calls"] += meta.get("usage_reported_calls", 0)
            self.metrics["response_model"] = meta.get("response_model")
            self.metrics["request_attempt_ids"].extend(meta.get("request_attempt_ids", []))
            for key in ("prompt_tokens", "completion_tokens"):
                if meta.get(key) is not None:
                    self.metrics[key] = (self.metrics[key] or 0) + meta[key]


def extract_sql(text: str) -> str:
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.I | re.S)
    return (match.group(1) if match else text).strip()
