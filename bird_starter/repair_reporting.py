"""Offline outcome summaries. Execution recovery and semantic evidence stay separate."""
from collections import Counter
from .reporting import latency_stats


def repair_summary(records):
    assessed = [r for r in records if not r.get("dry_run")]
    repaired = [r for r in assessed if r.get("repair_attempts", 0)]
    actual = [r for r in repaired if r.get("node_metrics", {}).get("repair", {}).get("model_calls", 0) > 0]
    verified = [r for r in actual if r.get("repair_success") is not None]
    stats = [r.get("node_metrics", {}).get("repair", {}) for r in repaired]
    calls = sum(s.get("model_calls", 0) for s in stats)
    def tokens(key):
        return sum(s[key] or 0 for s in stats) if any(s.get(key) is not None for s in stats) or calls == 0 else None
    n, k = len(assessed), len(actual)
    return {
        "assessed_cases": n,
        "initial_sql_execution_successes": sum(r.get("initial_execution_ok") is True for r in assessed),
        "initial_sql_execution_success_rate": sum(r.get("initial_execution_ok") is True for r in assessed) / n if n else None,
        "final_sql_execution_successes": sum(r.get("execution_ok") is True for r in assessed),
        "final_sql_execution_success_rate": sum(r.get("execution_ok") is True for r in assessed) / n if n else None,
        "repair_trigger_count": sum(bool(r.get("repair_trigger")) for r in assessed),
        "repair_attempt_count": len(repaired), "repair_requested_cases": k, "repair_model_calls": calls,
        "repair_blocked_before_http_cases": len(repaired) - k,
        "repair_execution_recoveries": sum(bool(r.get("repair_execution_recovered")) for r in actual),
        "repair_execution_recovery_rate": sum(bool(r.get("repair_execution_recovered")) for r in actual) / k if k else None,
        "repair_failure_count": sum(r.get("repair_success") is False for r in actual),
        "repair_success_rate": (sum(r.get("repair_success") is True for r in actual) / k
                                if k and len(verified) == k else None),
        "repair_success_unknown_cases": k - len(verified),
        "error_category_distribution": dict(Counter(h["error_category"] for r in assessed
                                                    for h in r.get("execution_history", []) if h.get("error_category"))),
        "final_error_category_distribution": dict(Counter(r.get("error_category") or r.get("error_type")
                                                          for r in assessed if r.get("error_type"))),
        "per_case_model_calls": [{"case_id": r.get("case_id"), "model_calls": r.get("model_calls")} for r in assessed],
        "repair_prompt_tokens": tokens("prompt_tokens"), "repair_completion_tokens": tokens("completion_tokens"),
        "repair_usage_unknown_calls": calls - sum(s.get("usage_reported_calls", 0) for s in stats),
        "repair_latency_ms": latency_stats([s.get("latency_ms") for s in stats]),
        "total_latency_ms": latency_stats([r.get("latency_ms") for r in assessed]),
        "notice": "Repair success uses evaluator evidence only, not execution alone; local proxy is not official EX."
    }
