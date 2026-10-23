"""P1B usage/audit summaries. Unknown totals never become measured subtotals."""
from collections import Counter

from .adaptive_validation_budget import usage_cost
from .reporting import latency_stats


def aggregate(rows, events, execution, pricing=None):
    result = {}
    for group in ("B", "E"):
        group_rows = rows[group]
        attempts = [a for a in events if a["case_id"].startswith(group + ":")]
        known = [a for a in attempts if type(a.get("prompt_tokens")) is int
                 and type(a.get("completion_tokens")) is int]
        missing = len(attempts) - len(known)
        prompt = sum(a["prompt_tokens"] for a in known)
        completion = sum(a["completion_tokens"] for a in known)
        costs = [usage_cost(a, pricing) if pricing else None for a in attempts]
        measurable = sum(c is not None for c in costs)
        nodes = {}
        for node in ("linking", "generation"):
            selected = [a for a in attempts if a["case_id"].endswith(":" + node)]
            node_known = [a for a in selected if type(a.get("prompt_tokens")) is int
                          and type(a.get("completion_tokens")) is int]
            metric_key = "schema_linking" if node == "linking" else "generation"
            values = [r["node_metrics"][metric_key]["latency_ms"] for r in group_rows
                      if r.get("node_metrics", {}).get(metric_key, {}).get("model_calls", 0) > 0]
            nodes[node] = {"reservations": len(selected), "usage_missing_calls": len(selected) - len(node_known),
                           "prompt_tokens": sum(a["prompt_tokens"] for a in node_known) if len(selected) == len(node_known) else None,
                           "completion_tokens": sum(a["completion_tokens"] for a in node_known) if len(selected) == len(node_known) else None,
                           "latency_ms": {**latency_stats(values), "n": len(values)}}
        latencies = [r.get("total_latency_ms") for r in group_rows if r["processed"]]
        result[group] = {"measurement_kind": {"live": "REAL_MODEL", "mock": "OFFLINE_MOCK_NOT_MODEL_QUALITY", "dry-run": "DRY_RUN"}[execution],
            "planned_cases": 10, "processed_cases": sum(r["processed"] for r in group_rows),
            "official_ex": None, "official_ex_status": "NOT_EVALUATED",
            "request_reservations": len(attempts), "sdk_model_calls": sum(r["model_calls"] for r in group_rows),
            "explicit_http_responses": sum(type(a.get("http_status")) is int for a in attempts),
            "transmission_count": (len(attempts) if attempts and all(type(a.get("http_status")) is int for a in attempts) else (0 if not attempts else "UNKNOWN")),
            "provider_billable_requests": "UNKNOWN", "failed_requests": sum(a["result_status"] == "FAILED" for a in attempts),
            "unknown_outcomes": sum(a["result_status"] == "INDETERMINATE" for a in attempts),
            "usage_missing_calls": missing, "usage_coverage": len(known) / len(attempts) if attempts else None,
            "known_prompt_tokens_subtotal": prompt, "known_completion_tokens_subtotal": completion,
            "prompt_tokens": prompt if attempts and not missing else None,
            "completion_tokens": completion if attempts and not missing else None,
            "total_tokens": prompt + completion if attempts and not missing else None,
            "usage_source": "PROVIDER_USAGE" if execution == "live" else "MOCK_OR_NO_USAGE_NOT_PROVIDER_MEASUREMENT",
            "total_tokens_method": "sum_of_reported_prompt_and_completion; not an independently returned total field",
            "nodes": nodes, "agent_latency_ms": {**latency_stats(latencies), "n": len(latencies)},
            "latency_policy": "linear interpolation; all processed cases including failed/timeouts; unrun excluded with count",
            "execution_successes": sum(r.get("execution_ok") is True for r in group_rows),
            "result_truncations": sum(r.get("output_truncated") is True for r in group_rows),
            "generation_truncations": sum(r.get("error_type") == "model_output_truncated" for r in group_rows),
            "errors": dict(Counter(r.get("error_type") for r in group_rows if r.get("error_type"))),
            "routing": dict(Counter(r.get("schema_routing", {}).get("routing_decision", "NOT_RUN") for r in group_rows)),
            "calculated_cost_usd": str(sum(costs)) if attempts and measurable == len(attempts) else None,
            "calculated_cost_status": "NOT_APPLICABLE_SUBSCRIPTION_ALLOCATION" if pricing and pricing.get("billing_mode") == "included_subscription" else "USAGE_TIMES_VERIFIED_RATES_OR_UNKNOWN",
            "known_calculated_cost_subtotal_usd": str(sum(c for c in costs if c is not None)),
            "unmeasurable_requests": len(attempts) - measurable,
            "cost_coverage": measurable / len(attempts) if attempts else None,
            "billed_cost_usd": "UNKNOWN", "pricing": pricing}
    return result
