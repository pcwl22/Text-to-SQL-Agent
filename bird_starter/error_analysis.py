"""Offline classification of saved evidence; never runs SQL, Agents, or models."""
from collections import Counter
import json
from pathlib import Path

from .experiment_review import read_group, reserve_private_output, PROJECT_ROOT
from .experiment_sets import file_sha256
from .observability import OBSERVABILITY_VERSION, text_sha256
from .model import redact
from .official_ex_inputs import OFFICIAL_COMMIT, PROTOCOL
from .reporting import latency_stats
from .request_audit import write_new_json

ANALYSIS_VERSION = "offline-error-analysis-v1"
MANUAL_CATEGORIES = frozenset({"schema_missing", "join_error", "filter_omission", "filter_extra",
                               "aggregation_error", "unknown"})


def observation_status(row):
    """Legacy hashes cannot restore missing projections. Validate new payloads."""
    evidence = row.get("schema_observation")
    if evidence is None:
        return "UNKNOWN_NOT_RECORDED"
    if not isinstance(evidence, dict) or evidence.get("version") != OBSERVABILITY_VERSION:
        raise ValueError("Unsupported schema observation")
    schema, selection = evidence.get("selected_schema"), evidence.get("selection")
    if (not isinstance(schema, str) or not isinstance(selection, dict)
            or not isinstance(selection.get("tables"), dict)
            or not selection["tables"]
            or any(not isinstance(t, str) or not isinstance(cols, list) or not cols
                   or any(not isinstance(c, str) for c in cols) for t, cols in selection["tables"].items())
            or set(selection["tables"]) != set(row.get("selected_tables", []))
            or text_sha256(schema) != evidence.get("persisted_schema_sha256")):
        raise ValueError("Invalid schema observation or selection identity")
    exact = evidence.get("persistence_status") == "EXACT"
    if evidence.get("persistence_status") not in {"EXACT", "REDACTED"}:
        raise ValueError("Unknown schema persistence status")
    if exact != (evidence.get("selected_schema_sha256") == evidence["persisted_schema_sha256"]):
        raise ValueError("Schema redaction/hash mismatch")
    metrics = row.get("schema_linking") or {}
    for key in ("selected_schema_sha256", "full_schema_sha256"):
        if metrics.get(key) is not None and metrics[key] != evidence.get(key):
            raise ValueError("Schema observation differs from node evidence")
    return "EXACT" if exact else "REDACTED_NOT_RECONSTRUCTABLE"


def read_official(path, context):
    """Bind scores to exact source bytes, ordered identities and frozen protocol."""
    summary = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    upstream = json.loads((PROJECT_ROOT / "configs/official_ex/upstream.json").read_text(encoding="utf-8"))
    source_hashes = {name: info["sha256"] for name, info in upstream["files"].items()}
    rows, metadata = summary.get("rows", []), context["metadata"]
    if (summary.get("status") != "COMPLETED" or summary.get("protocol") != PROTOCOL
            or summary.get("official_commit") != OFFICIAL_COMMIT
            or summary.get("official_source_sha256") != source_hashes
            or summary.get("scoring_logic_modified") is not False
            or summary.get("source_result_sha256") != context["source_result_sha256"]
            or context["source_result_sha256"] is None
            or summary.get("manifest_sha256") != context["config"]["manifest_sha256"]
            or summary.get("measurement_kind") != context["rows"][0]["measurement_kind"]
            or summary.get("planned_cases") != len(metadata)
            or [(r.get("case_id"), r.get("db_id")) for r in rows]
               != [(r["case_id"], r["db_id"]) for r in metadata]
            or any(type(r.get("res")) is not int or r["res"] not in (0, 1) for r in rows)
            or any(r["res"] == 1 and r.get("status") != "OK" for r in rows)
            or summary.get("correct") != sum(r["res"] for r in rows)):
        raise ValueError("Official scores do not match the saved run/protocol")
    return {r["case_id"]: {"res": r["res"], "status": r.get("status", "UNKNOWN")} for r in rows}


def classify_record(row, official=None):
    """Classify observations, not inferred semantic causes or repair eligibility."""
    error = row.get("error_category") or row.get("execution_error")
    if not row.get("processed"):
        return "not_run_or_missing"
    if row.get("measurement_kind") == "DRY_RUN":
        return "dry_run"
    if not row.get("generation_success"):
        return "generation_truncated" if error == "model_output_truncated" else "generation_failure"
    if row.get("output_truncated"):
        return "result_truncated"
    if row.get("final_execution_ok") is not True:
        if error in {"syntax", "sql_syntax_error"}:
            return "syntax_error"
        if error in {"timeout", "sql_timeout"}:
            return "execution_timeout"
        if error in {"missing_table", "missing_column", "ambiguous_column", "sql_schema_error"}:
            return "schema_execution_error"
        if error in {"safety_rejected", "sql_safety_error"}:
            return "safety_rejected"
        return "execution_or_repair_failure"
    if official is not None:
        if official["res"] == 1:
            return "official_match"
        if official["status"] == "MISMATCH":
            return "semantic_mismatch_unattributed"
        return "official_evaluation_failure_or_unknown"
    if row.get("local_result_match_proxy") is True:
        return "proxy_match_only"
    if row.get("local_result_match_proxy") is False:
        # Historical evaluator merges mismatch and evaluator error into False.
        return "proxy_unmatched_or_evaluator_error"
    return "unassessable"


def node_summary(rows):
    result = {}
    for name in ("schema_linking", "generation", "repair"):
        stats = [r.get("node_metrics", {}).get(name) for r in rows]
        known = [s for s in stats if s is not None]
        result[name] = {"recorded_cases": len(known), "planned_cases": len(rows),
                        "latency_ms": latency_stats([s.get("latency_ms") for s in known])}
        for key in ("model_calls", "prompt_tokens", "completion_tokens"):
            values = [s.get(key) for s in known]
            subtotal = sum(v for v in values if type(v) is int)
            result[name]["known_" + key + "_subtotal"] = subtotal
            result[name][key] = subtotal if len(known) == len(rows) and all(type(v) is int for v in values) else None
    return result


def read_annotations(path, contexts):
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    result = {}
    for item in payload["annotations"]:
        group = item["experiment_group"]
        context = contexts.get(group)
        identity = next((r for r in context["metadata"] if r["case_id"] == item["case_id"]), None) if context else None
        key = (group, item["case_id"])
        if (key in result or identity is None or item.get("db_id") != identity["db_id"]
                or not context["source_result_sha256"]
                or item.get("source_result_sha256") != context["source_result_sha256"]
                or item.get("category") not in MANUAL_CATEGORIES
                or item.get("confidence") not in {"CONFIRMED", "LIKELY", "UNKNOWN"}
                or any(not isinstance(item.get(k), str) or not 0 < len(item[k].strip()) <= 2000
                       for k in ("evidence", "reviewer"))):
            raise ValueError("Invalid, duplicate or unbound manual attribution")
        result[key] = {k: item[k] for k in ("category", "confidence", "evidence", "reviewer")}
    return result


def build_error_report(runs, suite_path, out_dir, *, official_summaries=None, annotations=None):
    if not runs or any(g not in {"A", "B", "C", "D"} for g in runs):
        raise ValueError("Select A/B/C/D groups")
    if set(official_summaries or {}) - set(runs):
        raise ValueError("Official scores supplied for an unselected group")
    # Bracket reads, including missing result files: a file created/changed while
    # reading must not produce an apparently immutable report from stale data.
    paths = {Path(suite_path).resolve(), PROJECT_ROOT / "configs/official_ex/upstream.json"}
    paths.update(Path(p).resolve() for p in (official_summaries or {}).values())
    if annotations:
        paths.add(Path(annotations).resolve())
    suite = json.loads(Path(suite_path).read_text(encoding="utf-8-sig"))
    paths.add((PROJECT_ROOT / suite["manifest"]).resolve())
    for group, folder in runs.items():
        paths.update((Path(folder) / name).resolve() for name in
                     ("batch.config.json", "batch.attempts.jsonl", f"{group}.jsonl"))
    inputs = {str(p): file_sha256(p) if p.exists() else None for p in paths}
    contexts = {g: read_group(path, g, suite_path) for g, path in runs.items()}
    if len({c["rows"][0]["measurement_kind"] for c in contexts.values()}) != 1:
        raise ValueError("Do not mix Mock/dry-run/real evidence")
    manual = read_annotations(annotations, contexts)
    groups, cases = {}, []
    for group, context in contexts.items():
        official_path = (official_summaries or {}).get(group)
        official = read_official(official_path, context) if official_path else {}
        details = []
        for row in context["rows"]:
            versions = row.get("prompt_versions")
            if versions is not None and versions != {k: context["batch"]["common"][k] for k in
                    ("prompt_version", "schema_link_prompt_version", "repair_prompt_version")}:
                raise ValueError("Prompt version evidence differs from the frozen run")
            score = official.get(row["case_id"])
            outcome = classify_record(row, score)
            attribution = manual.get((group, row["case_id"]))
            if attribution and outcome != "semantic_mismatch_unattributed":
                raise ValueError("Semantic annotations require a complete official mismatch")
            recall = row.get("schema_recall") or {}
            details.append({"experiment_group": group, "case_id": row["case_id"], "db_id": row["db_id"],
                "difficulty": row.get("difficulty"), "outcome": outcome,
                "schema_evidence_status": observation_status(row),
                "schema_dependency_missing_observed": bool(recall.get("missing_tables") or recall.get("missing_columns")),
                "schema_dependency_missing_is_proven_cause": False,
                "schema_fallback": row.get("schema_fallback"),
                "fallback_reason": (row.get("schema_linking") or {}).get("fallback_reason"),
                "official_result": score, "manual_attribution": attribution,
                "repair_trigger": row.get("repair_trigger"), "repair_attempts": row.get("repair_attempts"),
                "prompt_versions": row.get("prompt_versions"),
                "node_metrics": row.get("node_metrics", {})})
        cases.extend(details)
        groups[group] = {"planned_cases": len(details), "source_run_id": context["batch"]["run_id"],
            "source_result_sha256": context["source_result_sha256"],
            "outcome_counts": dict(Counter(r["outcome"] for r in details)),
            "schema_evidence_counts": dict(Counter(r["schema_evidence_status"] for r in details)),
            "official_correct": sum(s["res"] for s in official.values()) if official else None,
            "manual_attribution_count": sum(r["manual_attribution"] is not None for r in details),
            "manual_category_counts": dict(Counter(r["manual_attribution"]["category"] for r in details
                                                   if r["manual_attribution"] is not None)),
            "node_summary": node_summary(context["rows"]), "request_evidence": context["request_evidence"],
            "source_sha256": context["batch"].get("source_sha256"),
            "observability_version": context["batch"].get("observability_version")}
    report = {"version": ANALYSIS_VERSION, "measurement_kind": next(iter(contexts.values()))["rows"][0]["measurement_kind"],
        "analysis_kind": "OFFLINE_SAVED_EVIDENCE_NOT_A_NEW_EXPERIMENT", "groups": groups, "cases": cases,
        "input_sha256": inputs, "model_requests": 0, "sql_executions": 0,
        "notice": "Official labels are evaluator-only. Proxy False may include evaluator errors. "
                  "Missing dependencies are not causal proof. Manual attributions remain reviewer claims. "
                  "Legacy missing projections cannot be reconstructed. Observed cohort is development data."}
    # Verify all consumed files before reserving a new namespace. Never rewrite inputs.
    if any((file_sha256(path) if Path(path).exists() else None) != digest for path, digest in inputs.items()):
        raise ValueError("Analysis inputs changed during reading")
    report = redact(report)
    output = reserve_private_output(out_dir)
    write_new_json(output / "analysis.json", report)
    lines = ["# Offline error analysis", "", report["notice"], "",
             "| Group | Planned | Official correct | Outcomes | Schema evidence |",
             "| --- | --- | --- | --- | --- |"]
    for group, summary in groups.items():
        lines.append(f"| {group} | {summary['planned_cases']} | {summary['official_correct']} | "
                     f"{json.dumps(summary['outcome_counts'])} | {json.dumps(summary['schema_evidence_counts'])} |")
    with (output / "analysis.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return report
