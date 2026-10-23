"""Evaluator-only P1B adapter. Never imported by the generation entry point."""
import json
import os
from pathlib import Path
from decimal import Decimal

from .adaptive_validation_config import VERSION, read_json, validate_config
from .data import load_cases
from .experiment_review import reserve_private_output
from .experiment_sets import file_sha256
from .official_ex_inputs import (DELIMITER, FAILURE_SQL, PROTOCOL, engine_readiness,
                                 single_line_sql, validate_bundle)
from .request_audit import inspect_request_log, write_new_json
from .adaptive_validation_reporting import aggregate


def validated_results(run_dir, group, config_path):
    if group not in ("B", "E"):
        raise ValueError("P1B only evaluates B/E")
    folder = Path(run_dir)
    config = validate_config(read_json(config_path))
    batch = read_json(folder / "batch.config.json")
    cohort = read_json(folder / "frozen_manifest.json")
    preflight = read_json(folder / "preflight.json")
    if (batch["version"] != VERSION or batch["execution"] != "live" or batch["config"] != config
            or batch["frozen_config_sha256"] != file_sha256(config_path)
            or batch["frozen_manifest_sha256"] != config["manifest_sha256"]
            or file_sha256(folder / "frozen_manifest.json") != config["manifest_sha256"]
            or preflight["execution"] != "live" or preflight["run_id"] != batch["run_id"]
            or len(cohort["cases"]) != 10 or len({m["case_id"] for m in cohort["cases"]}) != 10
            or type(batch["max_model_calls"]) is not int or not 0 <= batch["max_model_calls"] <= 40):
        raise ValueError("P1B evaluation requires reconciled real results, frozen identity and denominator 10")
    expected_files = {"batch.config.json", "frozen_manifest.json", "batch.attempts.jsonl", "B.jsonl", "E.jsonl"}
    if not expected_files <= preflight["artifacts_sha256"].keys():
        raise ValueError("Incomplete finalized batch integrity record")
    for name, digest in preflight["artifacts_sha256"].items():
        if Path(name).name != name or file_sha256(folder / name) != digest:
            raise ValueError("Batch artifacts changed")
    audit = inspect_request_log(folder / "batch.attempts.jsonl", batch["run_id"], batch["max_model_calls"])
    if audit["audit_state"] != "VALID" or audit["prepared_attempts"] != preflight["reserved_attempts"]:
        raise ValueError("Request audit not reconciled")
    events = {a["attempt_id"]: a for a in audit["attempts"]}
    consumed, groups = set(), {}
    for g in ("B", "E"):
        with (folder / f"{g}.jsonl").open(encoding="utf-8") as stream:
            rows = []
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError("Torn result")
                rows.append(json.loads(line))
        if len(rows) != 10:
            raise ValueError("Fixed denominator/order requires all ten result slots")
        for meta, row in zip(cohort["cases"], rows, strict=True):
            if (any(row.get(k) != meta[k] for k in ("case_id", "db_id", "difficulty"))
                    or row["run_id"] != batch["run_id"] or row["experiment_group"] != g
                    or row["measurement_kind"] != "REAL_MODEL"
                    or any(k in row for k in ("SQL", "gold_sql", "prompt", "messages", "api_key"))):
                raise ValueError("Result identity/measurement mismatch")
            ids = row["request_attempt_ids"]
            if type(row["model_calls"]) is not int or not 0 <= row["model_calls"] <= 2 or len(ids) != row["model_calls"]:
                raise ValueError("Per-case request mismatch")
            node_names = []
            for ident in ids:
                event = events.get(ident)
                if ident in consumed or event is None:
                    raise ValueError("Duplicate/unlogged attempt")
                audit_group, case_id, node = event["case_id"].split(":")
                if audit_group != g or case_id != str(meta["case_id"]) or node not in ("linking", "generation"):
                    raise ValueError("Foreign attempt/node")
                consumed.add(ident)
                node_names.append(node)
            if len(set(node_names)) != len(node_names):
                raise ValueError("Node replay detected")
            if g == "E" and cohort["schema_distribution"][meta["db_id"]]["routing_decision"] == "SKIP_LINKING" and "linking" in node_names:
                raise ValueError("Unexpected adaptive Linking call")
        groups[g] = rows
    if consumed != events.keys():
        raise ValueError("Unattributed request reservations")
    return config, batch, cohort, audit, groups[group]


def export_inputs(run_dir, group, config_path, cases_path, out_dir):
    config, batch, cohort, audit, rows = validated_results(run_dir, group, config_path)
    if file_sha256(cases_path) != config["dataset_sha256"]:
        raise ValueError("Evaluator dataset hash mismatch")
    # This is the independent evaluator boundary; generation never calls load_cases.
    cases = {c["case_id"]: c for c in load_cases(cases_path)}
    predictions, gold, difficulties, alignment = {}, [], [], []
    for index, row in enumerate(rows):
        case = cases[row["case_id"]]
        if case["db_id"] != row["db_id"] or case.get("difficulty", "unknown") != row["difficulty"]:
            raise ValueError("Evaluator case mismatch")
        reason = None
        if not row["processed"]:
            reason = "missing_or_not_run"
        elif not row["generation_success"] or not row["execution_ok"] or row.get("error_type"):
            reason = "agent_failure_including_timeout"
        elif row.get("output_truncated") is not False:
            reason = "truncated_or_unknown_completeness"
        candidate = FAILURE_SQL
        if reason is None:
            try:
                candidate = single_line_sql(row["generated_sql"])
            except Exception:
                reason = "unsafe_or_unrepresentable_prediction"
        predictions[str(index)] = candidate + DELIMITER + case["db_id"]
        gold.append(single_line_sql(case["SQL"]) + "\t" + case["db_id"])
        difficulties.append(cohort["cases"][index])
        alignment.append({"index": index, "case_id": case["case_id"], "db_id": case["db_id"],
                          "forced_failure": reason is not None, "reason": reason})
    output = reserve_private_output(out_dir)
    write_new_json(output / "predictions.json", predictions)
    for name, lines in (("gold.sql", gold), ("difficulty.jsonl", [json.dumps(r) for r in difficulties])):
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    bundle = {"protocol": PROTOCOL, "run_id": batch["run_id"], "group": group, "planned_cases": 10,
              "measurement_kind": "REAL_MODEL", "dataset_version": config["dataset_version"],
              "dataset_sha256": config["dataset_sha256"], "manifest_sha256": config["manifest_sha256"],
              "source_result_sha256": file_sha256(Path(run_dir) / f"{group}.jsonl"),
              "request_evidence": audit, "alignment": alignment, "official_engine": engine_readiness(),
              "official_ex": None, "files_sha256": {p.name: file_sha256(p) for p in output.iterdir()}}
    write_new_json(output / "bundle.json", bundle)
    validate_bundle(output)
    return bundle


def compare_official(run_dir, config_path, b_summary, e_summary, out_dir):
    from .official_ex_inputs import OFFICIAL_COMMIT, PROTOCOL
    from .official_ex_windows import SOURCE_SHA256
    summaries = {"B": read_json(b_summary), "E": read_json(e_summary)}
    pairs, group_rows = {}, {}
    for g, summary in summaries.items():
        config, batch, cohort, audit, rows = validated_results(run_dir, g, config_path)
        group_rows[g] = rows
        if (summary["measurement_kind"] != "REAL_MODEL" or summary["planned_cases"] != 10
                or summary["source_result_sha256"] != file_sha256(Path(run_dir) / f"{g}.jsonl")
                or summary["manifest_sha256"] != config["manifest_sha256"]
                or summary["official_commit"] != OFFICIAL_COMMIT or summary["protocol"] != PROTOCOL
                or summary["official_source_sha256"] != SOURCE_SHA256 or summary["scoring_logic_modified"] is not False
                or len(summary["rows"]) != 10):
            raise ValueError("Official result provenance mismatch")
        for index, (row, meta) in enumerate(zip(summary["rows"], cohort["cases"], strict=True)):
            if (row["case_id"] != meta["case_id"] or row["db_id"] != meta["db_id"] or row["sql_idx"] != index
                    or type(row["res"]) is not int or row["res"] not in (0, 1)):
                raise ValueError("Official result alignment mismatch")
            pairs.setdefault(index, {**meta})[g] = row["res"]
        correct = sum(r["res"] for r in summary["rows"])
        if summary["correct"] != correct or summary["official_ex"] != correct / 10:
            raise ValueError("Official numerator mismatch")
    result = {"planned_cases": 10, "paired_rows": list(pairs.values()),
              "B": summaries["B"]["correct"], "E": summaries["E"]["correct"],
              "notice": "New questions on the same databases; small paired sample, no general significance claim."}
    stats = aggregate(group_rows, audit["attempts"], "live", config["pricing"] if config["pricing"]["status"] == "VERIFIED" else None)
    for g in ("B", "E"):
        stats[g].update(official_ex=summaries[g]["official_ex"], official_correct=summaries[g]["correct"],
                        official_denominator=10, official_ex_status="INDEPENDENT_OFFICIAL_EVALUATION")
    comparison = {}
    def metric(name, b, e):
        delta = e - b if type(b) in (int, float) and type(e) in (int, float) else None
        if name == "calculated_cost_usd" and b is not None and e is not None:
            delta = str(Decimal(e) - Decimal(b))
        comparison[name] = {"B": b, "E": e, "difference_E_minus_B": delta}
    metric("official_ex", summaries["B"]["official_ex"], summaries["E"]["official_ex"])
    for name in ("prompt_tokens", "completion_tokens", "total_tokens", "calculated_cost_usd", "billed_cost_usd"):
        metric(name, stats["B"][name], stats["E"][name])
    for name in ("average", "p50", "p95"):
        metric("agent_latency_ms_" + name, stats["B"]["agent_latency_ms"][name], stats["E"]["agent_latency_ms"][name])
    metric("linking_request_reservations", stats["B"]["nodes"]["linking"]["reservations"], stats["E"]["nodes"]["linking"]["reservations"])
    result.update(groups=stats, comparison=comparison,
                  official_summary_sha256={"B": file_sha256(b_summary), "E": file_sha256(e_summary)})
    output = reserve_private_output(out_dir)
    write_new_json(output / "comparison.json", result)
    with (output / "comparison.md").open("x", encoding="utf-8") as stream:
        stream.write("| Metric | B | E | E - B |\n| --- | --- | --- | --- |\n")
        for name, item in comparison.items():
            show = lambda value: "UNKNOWN" if value is None else str(value)
            stream.write(f"| {name} | {show(item['B'])} | {show(item['E'])} | {show(item['difference_E_minus_B'])} |\n")
        stream.write("\nLatency uses processed cases, including failures/timeouts; sample counts and Usage coverage are in comparison.json.\n")
        stream.write(result["notice"] + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return result
