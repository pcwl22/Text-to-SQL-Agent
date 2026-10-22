"""Offline Gate planning: metadata, saved P0 evidence and dry-run graphs only."""
from collections import Counter
import json
import math
from pathlib import Path

from .baseline import run_baseline
from .data import AgentInput, find_db, validate_db_id
from .error_analysis import observation_status, node_summary
from .experiment_review import read_group, expected_metadata, reserve_private_output, PROJECT_ROOT
from .experiment_sets import file_sha256
from .observability import text_sha256
from .prompts import generation_prompt
from .reporting import latency_stats
from .request_audit import write_new_json
from .schema_linking import read_schema, linking_prompt, render_selection, full_selection
from .schema_routing import AdaptiveSchemaGate, ESTIMATION_METHOD, load_gate, route_schema
from .schema_workflow import build_schema_graph, schema_graph_record


def estimate_prompt(messages):
    """Content bytes only; no tokenizer or provider/chat-envelope measurement."""
    return math.ceil(sum(len(message.encode("utf-8")) for message in messages) / 4)


def project_inputs(path):
    """Discard all fields outside the generation whitelist at JSON decode time."""
    allowed = ("question_id", "case_id", "db_id", "question", "evidence", "difficulty")
    rows = json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_hook=lambda obj: {k: obj[k] for k in allowed if k in obj})
    if not isinstance(rows, list):
        raise ValueError("Expected a classic JSON case list")
    result, identities = {}, set()
    for index, row in enumerate(rows):
        identity = row.get("question_id", row.get("case_id", index))
        if (not ((type(identity) is int and identity >= 0) or (isinstance(identity, str) and identity.strip()))
                or str(identity) in identities or ("case_id" in row and row["case_id"] != identity)
                or any(not isinstance(row.get(k), str) for k in ("question", "evidence", "db_id"))
                or not row["question"].strip()):
            raise ValueError("Invalid generation input identity/fields")
        validate_db_id(row["db_id"])
        identities.add(str(identity))
        result[identity] = row
    return result


def threshold_basis(distribution):
    """Largest multiplicative adjacent gap, once per unique database, size-only."""
    sizes = sorted({r["full_schema_size"] for r in distribution})
    if len(sizes) < 2:
        return None
    lower, upper = max(zip(sizes, sizes[1:]), key=lambda pair: (pair[1] / pair[0], -pair[0]))
    return {"method": "largest_adjacent_size_ratio_lower_endpoint_inclusive",
            "max_full_schema_bytes": lower, "next_schema_bytes": upper, "ratio": upper / lower,
            "uses_question_gold_or_scores": False}


def selection_overhead(selection):
    """Canonical contract size; historical raw model response bytes stay unknown."""
    tables = [{"name": name, "columns": columns} for name, columns in selection["requested_tables"].items()]
    joins = [{"left_table": r["left_table"], "left_column": r["left_columns"][0],
              "right_table": r["right_table"], "right_column": r["right_columns"][0]}
             for r in selection["relationships"] if r["kind"] == "model_join_columns_validated"]
    payload = {"tables": tables, "joins": joins}
    total = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    identifiers = [v for entry in tables for v in [entry["name"], *entry["columns"]]]
    identifiers.extend(v for entry in joins for v in entry.values())
    identifier_bytes = sum(len(json.dumps(v, ensure_ascii=False).encode("utf-8")) for v in identifiers)
    return {"canonical_contract_bytes": total, "canonical_identifier_bytes": identifier_bytes,
            "canonical_structure_bytes": total - identifier_bytes,
            "method": "reserialized_validated_request_not_original_model_response",
            "original_model_response_bytes": None}


def historical_usage(context):
    if not context["request_evidence"]["counts_complete"]:
        raise ValueError("Historical cost accounting is incomplete")
    rows = context["rows"]
    def total(field):
        values = [r.get(field) for r in rows]
        return sum(values) if all(type(v) is int for v in values) else None
    prompt, completion = total("prompt_tokens"), total("completion_tokens")
    return {"kind": "HISTORICAL_ACTUAL_NOT_ADAPTIVE", "run_id": context["batch"]["run_id"],
            "prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion if prompt is not None and completion is not None else None,
            "http_attempts": context["request_evidence"]["prepared_attempts"],
            "agent_latency_ms": latency_stats([r.get("total_latency_ms") for r in rows]),
            "nodes": node_summary(rows), "fallback_cases": sum(r.get("schema_fallback") is True for r in rows),
            "fallback_ratio": sum(r.get("schema_fallback") is True for r in rows) / len(rows),
            "denominator": len(rows), "actual_currency_cost": None}


class NoModel:
    def ask(self, *_):
        raise AssertionError("Offline cost planning must never invoke a model")


def analyze_costs(suite_path, cases_path, db_root, p0_run, a_run, b_run, gate_path, out_dir):
    """Never invokes run_suite, an evaluator, model construction or SQL execution."""
    config, metadata = expected_metadata(suite_path)
    gate = load_gate(gate_path)
    if not gate.enabled:
        raise ValueError("Comparison requires an explicit experimental Adaptive configuration")
    if file_sha256(cases_path) != config["dataset_sha256"]:
        raise ValueError("Dataset fingerprint differs")
    projected = project_inputs(cases_path)
    if len(projected) != config["dataset_question_count"]:
        raise ValueError("Dataset size differs")
    contexts = {"p0": read_group(p0_run, "B", suite_path), "A": read_group(a_run, "A", suite_path),
                "B": read_group(b_run, "B", suite_path)}
    if contexts["p0"]["batch"]["execution"] != "mock" or any(contexts[g]["batch"]["execution"] != "live" for g in "AB"):
        raise ValueError("Expected saved P0 Mock observations and historical real A/B")
    p0_rows = {r["case_id"]: r for r in contexts["p0"]["rows"]}
    b_rows = {r["case_id"]: r for r in contexts["B"]["rows"]}
    input_paths = [Path(p) for p in (suite_path, cases_path, gate_path)]
    input_paths.append(PROJECT_ROOT / config["manifest"])
    for folder, group in ((p0_run, "B"), (a_run, "A"), (b_run, "B")):
        input_paths.extend(Path(folder) / name for name in ("batch.config.json", "batch.attempts.jsonl", f"{group}.jsonl"))
    fingerprints = {str(p.resolve()): file_sha256(p) for p in input_paths}
    snapshots, paths, distribution = {}, {}, []
    for db_id in sorted({m["db_id"] for m in metadata}):
        path = find_db(db_root, db_id)
        if file_sha256(path) != config["database_sha256"][db_id]:
            raise ValueError("Database fingerprint differs")
        fingerprints[str(path.resolve())] = config["database_sha256"][db_id]
        snapshot = read_schema(path)
        snapshots[db_id], paths[db_id] = snapshot, path
        stats = route_schema(snapshot, gate)
        distribution.append({"db_id": db_id, "schema_table_count": stats["schema_table_count"],
            "schema_column_count": stats["schema_column_count"], "full_schema_size": stats["full_schema_size"],
            "full_schema_chars": len(snapshot["full_schema"]),
            "cases": sum(m["db_id"] == db_id for m in metadata), "routing_decision": stats["routing_decision"]})
    basis = threshold_basis(distribution)
    if basis is None or gate.max_full_schema_bytes != basis["max_full_schema_bytes"]:
        raise ValueError("Gate threshold must match the declared size-only distribution rule")
    common = config["common"]
    original = build_schema_graph(NoModel(), dry_run=True, max_prompt_chars=common["max_prompt_chars"])
    adaptive = build_schema_graph(NoModel(), dry_run=True, max_prompt_chars=common["max_prompt_chars"], adaptive_linking=gate)
    details, trajectories = [], []
    modes = {k: {"planned_cases": len(metadata), "scheduled_linking_requests": 0,
                "scheduled_generation_requests": len(metadata), "actual_linking_attempts": 0,
                "real_model_requests": 0, "linking_prompt_tokens_estimate": 0,
                "generator_preflight_full_schema_tokens_estimate": 0,
                "p0_mock_selection_scenario_generator_tokens_estimate": 0,
                "actual_ex": None, "actual_generator_output_tokens": None, "actual_latency_ms": None,
                "actual_currency_cost": None} for k in ("baseline", "schema_only", "adaptive")}
    for identity in metadata:
        case_id, db_id = identity["case_id"], identity["db_id"]
        case = projected[case_id]
        if case["db_id"] != db_id or case.get("difficulty") != identity.get("difficulty"):
            raise ValueError("Case identity differs")
        snapshot, p0 = snapshots[db_id], p0_rows[case_id]
        if observation_status(p0) != "EXACT":
            raise ValueError("P0 schema observation is unavailable or redacted")
        observation = p0["schema_observation"]
        if (render_selection(observation["selection"], snapshot) != observation["selected_schema"]
                or observation["full_schema_sha256"] != text_sha256(snapshot["full_schema"])
                or b_rows[case_id]["schema_linking"]["full_schema_sha256"] != observation["full_schema_sha256"]):
            raise ValueError("Saved/input schema differs from current metadata")
        agent = AgentInput.from_case(case, paths[db_id], "schema_only", no_evidence=not common["evidence"])
        link_prompt = linking_prompt(agent.question, agent.evidence, snapshot)
        full_prompt = generation_prompt(agent.question, agent.evidence, snapshot["full_schema"])
        selected_prompt = generation_prompt(agent.question, agent.evidence, observation["selected_schema"])
        routing = route_schema(snapshot, gate)
        use = routing["routing_decision"] == "USE_LINKING"
        generated = {
            "baseline": run_baseline(agent, NoModel(), dry_run=True, max_prompt_chars=common["max_prompt_chars"]),
            "schema_only": schema_graph_record(original.invoke(agent.to_state())),
            "adaptive": schema_graph_record(adaptive.invoke(agent.to_state()))}
        for mode, result in generated.items():
            if result["status"] != "dry_run_ok":
                raise ValueError("Offline preflight failed")
            evidence = result["schema_observation"]
            if evidence["selected_schema"] != snapshot["full_schema"]:
                raise ValueError("Dry-run must not fabricate a model selection")
            if mode == "baseline":
                evidence = {**evidence, "selection": full_selection(snapshot)}
                result["schema_observation"] = evidence
                result["schema_routing"] = route_schema(snapshot, AdaptiveSchemaGate(), linking_enabled=False)
                result["schema_routing"]["selected_schema_size"] = len(snapshot["full_schema"].encode("utf-8"))
            record = {"case_id": case_id, "db_id": db_id, "variant": mode,
                      "difficulty": identity.get("difficulty"),
                      "selected_tables": list(evidence["selection"]["tables"]),
                      "measurement_kind": "OFFLINE_PREFLIGHT_NOT_MODEL_QUALITY", **result}
            if observation_status(record) != "EXACT":
                raise ValueError("New preflight evidence is inconsistent")
            trajectories.append(record)
            count = mode == "schema_only" or (mode == "adaptive" and use)
            modes[mode]["scheduled_linking_requests"] += int(count)
            modes[mode]["actual_linking_attempts"] += result["schema_routing"]["actual_linking_attempts"]
            modes[mode]["linking_prompt_tokens_estimate"] += estimate_prompt(link_prompt) if count else 0
            modes[mode]["generator_preflight_full_schema_tokens_estimate"] += estimate_prompt(full_prompt)
            modes[mode]["p0_mock_selection_scenario_generator_tokens_estimate"] += estimate_prompt(selected_prompt if count else full_prompt)
        details.append({**identity, "schema_routing": generated["adaptive"]["schema_routing"],
            "full_generation_prompt_tokens_estimate": estimate_prompt(full_prompt),
            "linking_prompt_tokens_estimate": estimate_prompt(link_prompt),
            "linking_prompt_content_bytes": sum(len(s.encode("utf-8")) for s in link_prompt),
            "linking_input_schema_sha256": text_sha256(snapshot["full_schema"]),
            "p0_mock_selected_schema_bytes": len(observation["selected_schema"].encode("utf-8")),
            "p0_mock_selected_table_count": len(observation["selection"]["tables"]),
            "p0_mock_selected_column_count": sum(map(len, observation["selection"]["tables"].values())),
            "p0_mock_output_overhead": selection_overhead(observation["selection"]),
            "historical_fallback": b_rows[case_id]["schema_fallback"],
            "historical_selected_schema_chars": b_rows[case_id]["schema_linking"]["selected_schema_chars"],
            "historical_raw_selection": "UNKNOWN_NOT_RECORDED"})
    for mode in modes.values():
        mode["scheduled_total_requests"] = mode["scheduled_generation_requests"] + mode["scheduled_linking_requests"]
        mode["p0_mock_selection_scenario_total_input_tokens_estimate"] = mode["linking_prompt_tokens_estimate"] + mode["p0_mock_selection_scenario_generator_tokens_estimate"]
        mode["future_model_fallback_cases"] = None
    for name, mode in modes.items():
        mode["routing_counts"] = dict(Counter(r["schema_routing"]["routing_decision"] for r in trajectories if r["variant"] == name))
    skipped_ids = {r["case_id"] for r in details if r["schema_routing"]["linking_skipped"]}
    skipped_history = [b_rows[i]["node_metrics"]["schema_linking"] for i in skipped_ids]
    report = {"version": "p1a-offline-schema-cost-v1", "gate_config": json.loads(Path(gate_path).read_text(encoding="utf-8")),
        "threshold_basis": basis, "dataset_valid": True, "manifest_sha256": config["manifest_sha256"],
        "databases": sorted(distribution, key=lambda r: r["full_schema_size"]), "planned_cases": len(metadata),
        "routing_counts": dict(Counter(d["schema_routing"]["routing_decision"] for d in details)),
        "modes": modes, "cases": details, "historical_actual": {g: historical_usage(contexts[g]) for g in "AB"},
        "counterfactual_estimate": {"kind": "COUNTERFACTUAL_NOT_MEASURED_SAVINGS",
            "omitted_historical_linking_tokens": sum(s["prompt_tokens"] + s["completion_tokens"] for s in skipped_history),
            "adaptive_generator_tokens_latency_and_actual_cost": None,
            "notice": "Omitted linking usage is only a scenario; changed generator input/output may offset it."},
        "prompt_estimation_method": ESTIMATION_METHOD, "model_constructions": 0, "model_requests": 0,
        "new_sql_generated": 0, "actual_ex": "NOT_EVALUATED", "p0_exact_records_reconstructed": len(metadata),
        "new_preflight_records_reconstructed": len(trajectories),
        "input_sha256": fingerprints,
        "source_sha256": {name: file_sha256(PROJECT_ROOT / "bird_starter" / name) for name in
                          ("schema_routing.py", "schema_cost_analysis.py", "schema_workflow.py", "schema_linking.py",
                           "baseline.py", "observability.py", "prompts.py", "tools.py", "request_audit.py")},
        "recall_risk": "SKIP retains every schema column; semantic accuracy UNKNOWN. USE retains existing validation/fallback. "
                       "P0 Mock selections do not measure semantic recall. No Gold or score is used by the gate.",
        "notice": "All new estimates are content-byte heuristics without chat-envelope overhead. "
                  "Scenario totals reuse P0 Mock selections, not new Adaptive outputs. "
                  "Historical A/B scores are never assigned to Adaptive; future generator outputs, latency and fees UNKNOWN."}
    if any(file_sha256(p) != expected for p, expected in fingerprints.items()):
        raise ValueError("Analysis inputs changed during inspection")
    output = reserve_private_output(out_dir)
    write_new_json(output / "analysis.json", report)
    with (output / "preflight.jsonl").open("x", encoding="utf-8") as stream:
        for row in trajectories:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return report
