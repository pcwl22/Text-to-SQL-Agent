"""Generation-side evidence only. No case records, Gold, or evaluator access."""
import hashlib
from copy import deepcopy

from .model import redact

OBSERVABILITY_VERSION = "schema-observation-v1"


def text_sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def observe_schema(schema, selection=None, *, full_schema_sha256=None, fallback_reason=None):
    """Capture the prepared schema, including any validated JOIN hints.

    Hash original and persisted text separately: redaction must never be described
    as a byte-exact copy. This captures preparation, not proof of HTTP delivery.
    Arbitrary selector output and complete prompts are deliberately not accepted.
    """
    stored = redact(schema)
    selected = None if selection is None else redact(deepcopy({
        key: selection[key] for key in ("tables", "requested_tables", "relationships", "added_tables")
        if key in selection
    }))
    return {"version": OBSERVABILITY_VERSION,
            "selection": selected, "selected_schema": stored,
            "selected_schema_sha256": text_sha256(schema),
            "persisted_schema_sha256": text_sha256(stored),
            "full_schema_sha256": full_schema_sha256,
            "persistence_status": "EXACT" if schema == stored else "REDACTED",
            "fallback_reason": fallback_reason}


def model_call_metrics(model, started, ended):
    delegate = getattr(model, "delegate", model)
    meta = getattr(delegate, "last_call", {})
    return {"model_calls": meta.get("model_calls", 1),
            "latency_ms": round(meta.get("model_latency_ms", (ended - started) * 1000), 3),
            "prompt_tokens": meta.get("prompt_tokens"), "completion_tokens": meta.get("completion_tokens"),
            "usage_reported_calls": meta.get("usage_reported_calls", 0),
            "request_attempt_ids": list(meta.get("request_attempt_ids", []))}
