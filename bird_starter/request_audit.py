"""Append-only request reservations and read-only recovery. No SDK or prompts."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import uuid


AUDIT_VERSION = 1
RESULT_STATES = {"SUCCEEDED", "FAILED", "INDETERMINATE"}
TOKEN_DETAIL_FIELDS = {
    "prompt_tokens_details": ("cached_tokens", "audio_tokens"),
    "completion_tokens_details": ("reasoning_tokens", "audio_tokens", "accepted_prediction_tokens",
                                  "rejected_prediction_tokens"),
}


class AuditError(RuntimeError):
    def __init__(self, category="model_audit_error"):
        self.category = category
        super().__init__(category)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_text(value):
    if value is None or type(value) is int:
        return value
    text = str(value)
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+\S+", "[REDACTED]", text)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)[:256]


def write_new_json(path, value):
    """Exclusive create: never replace a manifest, recovery report or summary."""
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def safe_response_metadata(value):
    """Optional additive diagnostics: never persist raw choices, content or headers."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("parser_version", "response_model", "finish_reason"):
        if key in value:
            result[key] = safe_text(value[key]) if isinstance(value[key], str) else None
    for key in ("choices_count", "reasoning_tokens", "visible_content_chars"):
        if key in value:
            number = value[key]
            result[key] = number if type(number) is int and number >= 0 else None
    if "content_shape" in value:
        shape = value["content_shape"]
        result["content_shape"] = shape if shape in ("null", "text", "text_blocks", "unsupported") else None
    if type(value.get("reasoning_content_present")) is bool:
        result["reasoning_content_present"] = value["reasoning_content_present"]
    for name, fields in TOKEN_DETAIL_FIELDS.items():
        if name in value:
            source = value[name]
            result[name] = ({key: source[key] if type(source.get(key)) is int and source[key] >= 0 else None
                             for key in fields} if isinstance(source, dict) else None)
    return result


class RequestLog:
    """A fresh single-writer log. Existing logs are inspectable, never resumable."""
    def __init__(self, path, run_id, model_id, max_attempts):
        if type(max_attempts) is not int or max_attempts < 0:
            raise ValueError("max_attempts must be a non-negative integer")
        self.path = Path(path)
        self.run_id, self.model_id = safe_text(run_id), safe_text(model_id)
        self.max_attempts, self.reserved = max_attempts, 0
        self.poisoned = False
        self.pending = {}
        self.lock = threading.Lock()
        self.stream = self.path.open("x", encoding="utf-8")
        try:
            self._append({"event": "RUN_CREATED", "audit_version": AUDIT_VERSION,
                          "run_id": self.run_id, "model_id": self.model_id,
                          "max_http_attempts": max_attempts, "created_at_utc": utc_now()})
        except BaseException:
            self.stream.close()
            raise

    def _append(self, event):
        if self.poisoned or self.stream.closed:
            raise AuditError()
        try:
            line = json.dumps(event, ensure_ascii=False) + "\n"
            if self.stream.write(line) != len(line):
                raise OSError("Incomplete audit write")
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except BaseException as exc:
            self.poisoned = True
            if not isinstance(exc, Exception):
                raise
            raise AuditError() from None

    def prepare(self, case_id):
        with self.lock:
            attempt_id = uuid.uuid4().hex
            if self.poisoned or self.stream.closed or self.pending:
                raise AuditError()
            if self.reserved >= self.max_attempts:
                raise AuditError("model_budget_exceeded")
            self.reserved += 1  # Never refund even a partially written reservation.
            event = {"event": "ATTEMPT_PREPARED", "run_id": self.run_id,
                     "case_id": safe_text(case_id), "attempt_id": attempt_id,
                     "attempt_sequence": self.reserved, "model_id": self.model_id,
                     "prepared_at_utc": utc_now(), "result_status": "PREPARED",
                     "http_status": None, "provider_request_id": None,
                     "prompt_tokens": None, "completion_tokens": None,
                     "error_category": None, "response_completed_at_utc": None}
            self.pending[event["attempt_id"]] = event
            self._append(event)
            return event["attempt_id"]

    def finish(self, attempt_id, status, *, http_status=None, provider_request_id=None,
               prompt_tokens=None, completion_tokens=None, error_category=None, response_metadata=None):
        with self.lock:
            if status not in RESULT_STATES or attempt_id not in self.pending:
                raise AuditError()
            recorded_at = utc_now()
            event = dict(self.pending[attempt_id], event="ATTEMPT_RESULT", result_status=status,
                         http_status=http_status if type(http_status) is int else None,
                         provider_request_id=safe_text(provider_request_id),
                         prompt_tokens=prompt_tokens if type(prompt_tokens) is int and prompt_tokens >= 0 else None,
                         completion_tokens=completion_tokens if type(completion_tokens) is int and completion_tokens >= 0 else None,
                         error_category=safe_text(error_category), event_recorded_at_utc=recorded_at,
                         response_completed_at_utc=recorded_at if http_status is not None or status == "SUCCEEDED" else None)
            if response_metadata is not None:
                event["response_metadata"] = safe_response_metadata(response_metadata)
            self._append(event)
            del self.pending[attempt_id]
            if status == "INDETERMINATE":
                self.poisoned = True  # Do not retry another question either.

    def close(self):
        self.stream.close()


def inspect_request_log(path, run_id, max_attempts):
    """No writes or requests; incomplete/torn logs cannot restore authorization."""
    report = {"run_id": safe_text(run_id), "audit_state": "VALID", "attempts": [],
              "prepared_attempts": 0, "budget_limit": max_attempts,
              "automatic_resume_allowed": False, "automatic_requests_available": 0,
              "new_authorization_required": True, "actual_http_attempts": "UNKNOWN"}
    attempts = {}
    try:
        with Path(path).open(encoding="utf-8") as stream:
            header_line = stream.readline()
            header = json.loads(header_line)
            if (not header_line.endswith("\n") or header["event"] != "RUN_CREATED"
                    or header["audit_version"] != AUDIT_VERSION or header["run_id"] != run_id
                    or header["max_http_attempts"] != max_attempts):
                raise ValueError()
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError()
                event = json.loads(line)
                if event["run_id"] != run_id or event["model_id"] != header["model_id"]:
                    raise ValueError()
                ident = event["attempt_id"]
                if event["event"] == "ATTEMPT_PREPARED":
                    if ident in attempts or event["attempt_sequence"] != len(attempts) + 1:
                        raise ValueError()
                    if len(attempts) >= max_attempts or event["result_status"] != "PREPARED":
                        raise ValueError()
                    attempts[ident] = {k: safe_text(event[k]) for k in
                                       ("attempt_id", "attempt_sequence", "case_id", "model_id", "prepared_at_utc")}
                    attempts[ident].update(result_status="INDETERMINATE", result_event_present=False)
                elif event["event"] == "ATTEMPT_RESULT":
                    item = attempts[ident]
                    if (item["result_event_present"] or event["result_status"] not in RESULT_STATES
                            or any(event[k] != item[k] for k in
                                   ("attempt_sequence", "case_id", "model_id", "prepared_at_utc"))):
                        raise ValueError()
                    item.update({k: safe_text(event[k]) for k in
                                 ("result_status", "http_status", "provider_request_id", "prompt_tokens",
                                  "completion_tokens", "error_category", "response_completed_at_utc")})
                    item["result_event_present"] = True
                    if "response_metadata" in event:
                        item["response_metadata"] = safe_response_metadata(event["response_metadata"])
                else:
                    raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        report["audit_state"] = "INDETERMINATE"
    report["attempts"] = list(attempts.values())
    report["prepared_attempts"] = len(attempts)
    report["budget_reserved"] = max_attempts if report["audit_state"] != "VALID" else len(attempts)
    report["remaining_budget_before_authorization_check"] = max(0, max_attempts - report["budget_reserved"])
    report["indeterminate_attempts"] = sum(a["result_status"] == "INDETERMINATE" for a in attempts.values())
    report["http_responses_recorded"] = sum(type(a.get("http_status")) is int for a in attempts.values())
    report["notice"] = "PREPARED reserves budget; it does not prove transmission. No automatic replay or exactly-once guarantee."
    return report


def inspect_experiment(config_path):
    """Read a manifest and its derived sibling log; historical counts stay UNKNOWN."""
    config_path = Path(config_path)
    if not config_path.name.endswith(".config.json"):
        raise ValueError("Expected an experiment .config.json path")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        run_id = config["run_id"]
        cap = config["max_model_calls"]
        if not isinstance(run_id, str) or type(cap) is not int or cap < 0:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Cannot read experiment identity/budget; preserve files and do not send requests") from None
    log_path = config_path.with_name(config_path.name[:-len(".config.json")] + ".attempts.jsonl")
    if config.get("request_audit_version") != AUDIT_VERSION:
        return {"run_id": safe_text(run_id), "historical_http_attempts": "UNKNOWN",
                "audit_state": "HISTORICAL_UNKNOWN", "actual_http_attempts": "UNKNOWN",
                "budget_limit": cap, "budget_reserved": cap, "automatic_requests_available": 0,
                "automatic_resume_allowed": False, "new_authorization_required": True,
                "notice": "No historical request ledger; full budget blocked, not a measured request count."}
    report = inspect_request_log(log_path, run_id, cap)
    if config.get("attempt_log_file") != log_path.name:
        report.update(audit_state="INDETERMINATE", budget_reserved=cap,
                      remaining_budget_before_authorization_check=0)
    return report
