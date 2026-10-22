"""Deterministic pre-Linking routing using generation-side schema metadata only."""
from dataclasses import dataclass
import json
from pathlib import Path

GATE_VERSION = "adaptive-schema-gate-v1"
ESTIMATION_METHOD = "ceil_utf8_bytes_div_4_not_provider_usage"


@dataclass(frozen=True)
class AdaptiveSchemaGate:
    enabled: bool = False
    max_full_schema_bytes: int | None = None

    def __post_init__(self):
        if (type(self.enabled) is not bool
                or (self.max_full_schema_bytes is not None and
                    (type(self.max_full_schema_bytes) is not int or self.max_full_schema_bytes <= 0))
                or (self.enabled and self.max_full_schema_bytes is None)):
            raise ValueError("Adaptive Gate requires a boolean switch and a positive byte threshold")


def load_gate(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if (not isinstance(config, dict) or config.get("version") != GATE_VERSION
            or set(config) != {"version", "enabled", "max_full_schema_bytes", "threshold_basis"}
            or not isinstance(config["threshold_basis"], dict)):
        raise ValueError("Unsupported Adaptive Gate configuration")
    return AdaptiveSchemaGate(config["enabled"], config["max_full_schema_bytes"])


def route_schema(snapshot, gate, *, linking_enabled=True):
    """No question, identity, selection, score or Gold is accepted as a feature.

    Size is original DDL UTF-8 bytes. The inclusive threshold is the sole
    adaptive feature; counts are diagnostics, never additional routing rules.
    """
    if not isinstance(gate, AdaptiveSchemaGate) or type(linking_enabled) is not bool:
        raise ValueError("Invalid routing configuration")
    if snapshot is None:
        decision, reason, table_count, column_count, size = "NOT_RUN", "schema_unavailable", None, None, None
    else:
        schema, tables = snapshot["full_schema"], snapshot["tables"]
        if not isinstance(schema, str) or not schema.strip() or not isinstance(tables, dict) or not tables:
            raise ValueError("Schema metadata missing")
        table_count, column_count = len(tables), sum(len(t["columns"]) for t in tables.values())
        if column_count == 0:
            raise ValueError("Schema columns missing")
        size = len(schema.encode("utf-8"))
        if not linking_enabled:
            decision, reason = "SKIP_LINKING", "linking_disabled"
        elif gate.enabled and size <= gate.max_full_schema_bytes:
            decision, reason = "SKIP_LINKING", "schema_within_threshold"
        else:
            decision, reason = "USE_LINKING", "schema_above_threshold" if gate.enabled else "gate_disabled"
    return {"version": GATE_VERSION, "routing_mode": "ADAPTIVE" if gate.enabled else "LEGACY",
            "routing_decision": decision, "routing_reason": reason,
            "schema_table_count": table_count, "schema_column_count": column_count,
            "full_schema_size": size, "selected_schema_size": None, "schema_size_unit": "utf8_bytes",
            "max_full_schema_bytes": gate.max_full_schema_bytes,
            "linking_skipped": decision == "SKIP_LINKING" if decision != "NOT_RUN" else None,
            "actual_linking_attempts": 0, "linking_attempt_ids": [],
            "attempt_semantics": "adapter_sdk_attempts_not_delivery",
            "prompt_estimation_method": ESTIMATION_METHOD}
