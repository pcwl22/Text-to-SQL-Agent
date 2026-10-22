"""Small local summaries; execution success is not execution accuracy."""
from collections import Counter
import math


def latency_stats(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return {"average": None, "p50": None, "p95": None}
    def percentile(p):
        index = (len(values) - 1) * p
        low, high = math.floor(index), math.ceil(index)
        return values[low] + (values[high] - values[low]) * (index - low)
    return {"average": sum(values) / len(values), "p50": percentile(.5), "p95": percentile(.95)}


def estimated_cost(record, input_price, output_price):
    if record["model_calls"] == 0:
        return 0.0
    if (input_price is None or output_price is None
            or record["usage_reported_calls"] != record["model_calls"]
            or record["prompt_tokens"] is None or record["completion_tokens"] is None):
        return None
    return (record["prompt_tokens"] * input_price + record["completion_tokens"] * output_price) / 1000000


def summarize(records):
    assessed = [r for r in records if not r["dry_run"]]
    n = len(assessed)
    count = lambda field: sum(r.get(field) is True for r in assessed)
    errors = Counter(r["error_type"] for r in assessed if r.get("error_type"))
    rate = lambda numerator: numerator / n if n else None
    calls = sum(r["model_calls"] for r in records)
    reported = sum(r["usage_reported_calls"] for r in records)
    token_sum = lambda key: sum(r[key] or 0 for r in records) if any(
        r[key] is not None for r in records) or calls == 0 else None
    costs = [r["estimated_cost"] for r in records]
    proxy = [r["local_result_match_proxy"] for r in assessed if r["local_result_match_proxy"] is not None]
    return {
        "records": len(records), "assessed_cases": n,
        "dry_run_cases": sum(r["dry_run"] for r in records),
        "preflight_successes": sum(r.get("preflight_ok") is True for r in records),
        "generation_successes": count("generation_success"), "execution_successes": count("execution_ok"),
        "sql_generation_success_rate": rate(count("generation_success")),
        "sql_execution_success_rate": rate(count("execution_ok")),
        "sql_syntax_error_rate": rate(errors["sql_syntax_error"]),
        "sql_execution_timeout_rate": rate(errors["sql_timeout"]), "error_counts": dict(errors),
        "latency_ms": latency_stats([r["latency_ms"] for r in assessed]),
        "model_latency_ms": latency_stats([r["model_latency_ms"] for r in assessed]),
        "model_calls": calls, "average_model_calls": calls / n if n else None,
        "prompt_tokens": token_sum("prompt_tokens"), "completion_tokens": token_sum("completion_tokens"),
        "usage_reported_calls": reported, "usage_unknown_calls": calls - reported,
        "estimated_cost": sum(costs) if all(v is not None for v in costs) else None,
        "cost_unknown_cases": sum(v is None for v in costs),
        "local_result_match_proxy": {"matches": sum(v is True for v in proxy), "compared": len(proxy),
                                     "unknown": n - len(proxy), "official": False},
        "notice": "Execution success is NOT accuracy. Local proxy is NOT official BIRD EX.",
    }
