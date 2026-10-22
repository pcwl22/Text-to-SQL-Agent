"""Fixed-denominator ablation metrics and paired descriptive comparisons."""
from collections import Counter
from itertools import combinations

from .reporting import latency_stats


def summarize_group(rows, planned_ids):
    if [r["case_id"] for r in rows] != list(planned_ids) or len(set(planned_ids)) != len(planned_ids):
        raise ValueError("Results must retain every planned case once, in manifest order")
    n = len(planned_ids)
    dry = all(r["measurement_kind"] == "DRY_RUN" for r in rows)
    count = lambda key: sum(r.get(key) is True for r in rows)
    rate = lambda value: value / n if n and not dry else None
    calls = sum(r["model_calls"] for r in rows)
    missing = sum(r["usage_missing_calls"] for r in rows)
    selection = [r["schema_linking"] for r in rows if r.get("schema_linking")]
    attempted = [s for s in selection if s.get("selection_attempted")]
    recall = [r["schema_recall"] for r in rows if r.get("schema_recall")]
    required = lambda key: sum(s.get(key, 0) for s in recall)
    full = sum(s["full_schema_chars"] for s in selection)
    selected = sum(s["selected_schema_chars"] for s in selection)
    requested = [r for r in rows if r.get("node_metrics", {}).get("repair", {}).get("model_calls", 0)]
    tokens = lambda key: None if missing else sum(r.get(key) or 0 for r in rows)
    return {
        "planned_cases": n, "records": len(rows), "processed_cases": sum(r["processed"] for r in rows),
        "not_processed_cases": sum(not r["processed"] for r in rows),
        "mock_fault_injected_cases": sum(r.get("mock_fault_applied", False) for r in rows),
        "measurement_kind": rows[0]["measurement_kind"] if rows else None,
        "sql_generation_successes": count("generation_success"),
        "sql_generation_success_rate": rate(count("generation_success")),
        "initial_execution_successes": count("initial_execution_ok"),
        "initial_execution_success_rate": rate(count("initial_execution_ok")),
        "final_execution_successes": count("final_execution_ok"),
        "final_execution_success_rate": rate(count("final_execution_ok")),
        "local_result_match_proxy_matches": count("local_result_match_proxy"),
        "local_result_match_proxy_rate": rate(count("local_result_match_proxy")),
        "proxy_unknown_cases": sum(r["local_result_match_proxy"] is None for r in rows),
        "proxy_status_counts": dict(Counter(r["proxy_status"] for r in rows)),
        "schema_valid_selection_rate": (sum(s.get("selection_valid") is True for s in attempted) / len(attempted)
                                         if attempted else None),
        "schema_selection_attempted_cases": len(attempted),
        "schema_recall_evaluated_cases": len(recall),
        "schema_dependency_status_counts": dict(Counter(s["dependency_status"] for s in recall)),
        "schema_table_recall": required("recalled_table_count") / required("required_table_count")
                               if required("required_table_count") else None,
        "schema_column_recall": required("recalled_column_count") / required("required_column_count")
                                if required("required_column_count") else None,
        "schema_compression_ratio": 1 - selected / full if full and not dry else None,
        "schema_metrics_coverage": len(selection),
        "repair_trigger_count": count("repair_trigger"), "repair_trigger_rate": rate(count("repair_trigger")),
        "repair_attempts": sum(r["repair_attempts"] for r in rows), "repair_requested_cases": len(requested),
        "repair_execution_recoveries": sum(r["repair_execution_recovered"] for r in requested),
        "repair_recovery_rate": sum(r["repair_execution_recovered"] for r in requested) / len(requested) if requested else None,
        "repair_success_unknown_cases": sum(r["repair_success"] is None for r in requested),
        "error_category_counts": dict(Counter(r["error_category"] for r in rows if r["error_category"])),
        "latency_ms": latency_stats([r["total_latency_ms"] for r in rows]),
        "model_latency_ms": latency_stats([r["model_latency_ms"] for r in rows if r["processed"] and not dry]),
        "model_calls": calls, "model_calls_per_question": calls / n if n else None,
        "prompt_tokens": tokens("prompt_tokens"), "completion_tokens": tokens("completion_tokens"),
        "known_prompt_tokens_subtotal": sum(r.get("prompt_tokens") or 0 for r in rows),
        "known_completion_tokens_subtotal": sum(r.get("completion_tokens") or 0 for r in rows),
        "usage_missing_calls": missing,
        "estimated_cost": sum(r["estimated_cost"] for r in rows) if all(r["estimated_cost"] is not None for r in rows) else None,
        "official_ex": None,
        "notice": "Fixed planned denominator; execution is not accuracy; proxy is not official EX; Mock is not model quality."
    }


def paired_comparisons(groups):
    """Unknown outcomes stay unassessable; differences are descriptive, not causal."""
    results = {}
    for left, right in combinations(groups, 2):
        a, b = groups[left], groups[right]
        if [(r["case_id"], r["db_id"]) for r in a] != [(r["case_id"], r["db_id"]) for r in b]:
            raise ValueError("Paired comparison requires identical ordered cases and databases")
        if any(x["measurement_kind"] != y["measurement_kind"] or x["model_id"] != y["model_id"] or
               x["dataset_version"] != y["dataset_version"] or x["dataset_sha256"] != y["dataset_sha256"]
               for x, y in zip(a, b)):
            raise ValueError("Cannot pair different measurement kinds, models or dataset versions")
        rows = []
        for x, y in zip(a, b):
            p, q = x["local_result_match_proxy"], y["local_result_match_proxy"]
            outcome = ("unassessable" if p is None or q is None else "both_match" if p and q else
                       "left_only_match" if p else "right_only_match" if q else "neither_match")
            rows.append({"case_id": x["case_id"], "db_id": x["db_id"], "outcome": outcome,
                         "left_proxy": p, "right_proxy": q, "additional_model_calls_right": y["model_calls"] - x["model_calls"],
                         "left_initial_execution_ok": x["initial_execution_ok"],
                         "right_repair_execution_recovered": y["repair_execution_recovered"],
                         "execution_recovery_candidate": x["initial_execution_ok"] is False and y["repair_execution_recovered"]})
        results[f"{left}_vs_{right}"] = {"planned_pairs": len(rows), "outcome_counts": dict(Counter(r["outcome"] for r in rows)),
                                         "cases": rows, "causal_or_significance_claim": False}
    return results
