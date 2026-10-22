"""Recompute reports across independent group runs. No model or Gold access."""
import json
import os

from .ablation_reporting import summarize_group, paired_comparisons
from .experiment_review import read_group, reserve_private_output
from .official_ex_inputs import engine_readiness
from .request_audit import write_new_json


def build_report(runs, suite_path, out_dir):
    if not runs or any(g not in "ABCD" for g in runs):
        raise ValueError("Select distinct A/B/C/D runs")
    contexts = {g: read_group(path, g, suite_path) for g, path in runs.items()}
    kinds = {c["rows"][0]["measurement_kind"] for c in contexts.values()}
    if len(kinds) != 1:
        raise ValueError("Mock, dry-run and real model results cannot be combined")
    by_group, summaries = {}, {}
    for group, context in contexts.items():
        rows = context["rows"]
        for row in rows:
            row["official_ex"] = None
            row["official_ex_status"] = "PENDING_UPSTREAM_END_TO_END_VALIDATION"
        by_group[group] = rows
        summary = summarize_group(rows, [r["case_id"] for r in context["metadata"]])
        requested = [r for r in rows if r.get("node_metrics", {}).get("repair", {}).get("model_calls", 0)]
        summary.update(repair_proxy_successes=sum(r["repair_success"] is True for r in requested),
                       repair_proxy_failures=sum(r["repair_success"] is False for r in requested),
                       repair_proxy_success_rate=(sum(r["repair_success"] is True for r in requested) / len(requested)
                          if requested and all(r["repair_success"] is not None for r in requested) else None),
                       request_evidence=context["request_evidence"], missing_result_case_ids=context["missing_case_ids"],
                       source_run_id=context["batch"]["run_id"], official_ex_status="BLOCKED_END_TO_END_UNVERIFIED")
        if not context["request_evidence"]["counts_complete"]:
            summary["known_usage_missing_calls"] = summary["usage_missing_calls"]
            for field in ("model_calls", "model_calls_per_question", "prompt_tokens", "completion_tokens", "estimated_cost", "usage_missing_calls"):
                summary[field] = None
        summaries[group] = summary
    pairs = paired_comparisons(by_group)
    for name, pair in pairs.items():
        a, b = name.split("_vs_")
        if not summaries[a]["request_evidence"]["counts_complete"] or not summaries[b]["request_evidence"]["counts_complete"]:
            for row in pair["cases"]:
                row["additional_model_calls_right"] = None
        for row in pair["cases"]:
            row.update(left_official_ex=None, right_official_ex=None, official_ex_change="pending")
    first = next(iter(contexts.values()))
    report = {"measurement_kind": next(iter(kinds)), "manifest_sha256": first["config"]["manifest_sha256"],
              "dataset_version": first["config"]["dataset_version"], "planned_cases_per_group": len(first["metadata"]),
              "groups": summaries, "paired_comparisons": pairs, "official_engine": engine_readiness(),
              "notice": "Fixed planned denominators. Mock is not model quality; proxy is not official EX; unknown requests stay unknown."}
    output = reserve_private_output(out_dir)
    write_new_json(output / "report.json", report)
    with (output / "per_case.jsonl").open("x", encoding="utf-8") as stream:
        for rows in by_group.values():
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    lines = ["# Ablation review", "", f"Measurement: {report['measurement_kind']}. Official EX: BLOCKED / null.", "",
             "| Group | Generated | Initial execution | Final execution | Local Proxy | Model attempts | Tokens | Official EX |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for group, summary in summaries.items():
        n = summary["planned_cases"]
        lines.append(f"| {group} | {summary['sql_generation_successes']}/{n} | {summary['initial_execution_successes']}/{n} | "
                     f"{summary['final_execution_successes']}/{n} | {summary['local_result_match_proxy_matches']}/{n} | "
                     f"{summary['model_calls']} | {summary['prompt_tokens']}/{summary['completion_tokens']} | pending |")
    lines += ["", "Full schema/repair, per-case calls, usage completeness, latency mean/P50/P95 and paired outcomes: report.json / per_case.jsonl.",
              "Unknown Proxy results remain in the planned denominator. No causal or significance claim."]
    with (output / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return report
