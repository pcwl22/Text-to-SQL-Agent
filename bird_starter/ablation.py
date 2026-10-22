"""Versioned, serial four-group experiments; reuse existing Agents and audit."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
from types import SimpleNamespace as NS

from .ablation_evaluation import OfflineEvaluator
from .ablation_mock import MockCompletions
from .ablation_reporting import summarize_group, paired_comparisons
from .baseline import error_info, run_baseline
from .data import AgentInput, load_cases, find_db
from .experiment_sets import file_sha256, select_manifest_cases
from .model import Model, TrackedModel, redact, safe_base_url, RESPONSE_PARSER_VERSION
from .prompts import PROMPT_VERSION
from .reporting import estimated_cost
from .request_audit import RequestLog, AuditError, write_new_json, inspect_request_log, AUDIT_VERSION
from .schema_linking import read_schema, full_selection, schema_metrics, LINK_PROMPT_VERSION
from .schema_workflow import build_schema_graph, schema_graph_record
from .sql_repair import REPAIR_PROMPT_VERSION
from run_eval import ISOLATED_CASE_ERRORS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GROUPS = {"A": ("baseline", False, False), "B": ("schema_only", True, False),
          "C": ("repair_only", False, True), "D": ("full", True, True)}


class GroupRequestLog:
    """Group cap over one durable batch ledger; PREPARED must fsync before SDK.

    Audit case_id is group:case_id to disambiguate repeated samples. Returned
    attempt IDs are the same IDs persisted in group results and the batch log.
    The original RequestLog implementation is unchanged.
    """
    def __init__(self, batch_log, group, cap):
        self.batch_log, self.group = batch_log, group
        self.max_attempts, self.model_id, self.reserved = cap, batch_log.model_id, 0

    def prepare(self, case_id):
        if self.reserved >= self.max_attempts:
            raise AuditError("model_budget_exceeded")
        self.reserved += 1
        return self.batch_log.prepare(f"{self.group}:{case_id}")

    def finish(self, attempt_id, status, **metadata):
        return self.batch_log.finish(attempt_id, status, **metadata)


@contextmanager
def offline_dotenv(disabled):
    previous = os.environ.get("PYTHON_DOTENV_DISABLED")
    if disabled:
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    try:
        yield
    finally:
        if disabled:
            if previous is None:
                os.environ.pop("PYTHON_DOTENV_DISABLED", None)
            else:
                os.environ["PYTHON_DOTENV_DISABLED"] = previous


def load_configuration(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if config.get("suite_version") != "phase3c-ablation-v1":
        raise ValueError("Unsupported ablation configuration version")
    if config.get("evaluation_version") != "phase3c-fixed-denominator-v1":
        raise ValueError("Evaluation version mismatch")
    if set(config["groups"]) != set(GROUPS):
        raise ValueError("Configuration must define exactly four known groups")
    for g, (mode, linking, repair) in GROUPS.items():
        item = config["groups"][g]
        ceiling = config["case_count"] * (1 + linking + repair)
        if (item["mode"] != mode or item["schema_linking"] is not linking or item["repair"] is not repair
                or type(item["max_http_attempts"]) is not int or not 0 <= item["max_http_attempts"] <= ceiling):
            raise ValueError("Group definition or budget differs from the implemented strategy")
    if (type(config["batch_max_http_attempts"]) is not int or not 0 <= config["batch_max_http_attempts"] <=
            sum(g["max_http_attempts"] for g in config["groups"].values())):
        raise ValueError("Invalid configured batch cap")
    for reference, digest in config.get("source_profiles", {}).items():
        if file_sha256(PROJECT_ROOT / reference) != digest:
            raise ValueError("Frozen source profile changed")
    common = config["common"]
    if (common["request_retries"] != 0 or common["sdk_retries"] != 0 or common["concurrency"] != 1
            or common["http_redirects"] is not False or common["max_repairs"] != 1
            or common["n"] != 1 or type(common["evidence"]) is not bool):
        raise ValueError("Ablation requires serial execution, one repair, zero retries and redirects")
    if (common["prompt_version"] != PROMPT_VERSION or common["schema_link_prompt_version"] != LINK_PROMPT_VERSION
            or common["repair_prompt_version"] != REPAIR_PROMPT_VERSION):
        raise ValueError("Recorded prompt versions do not match installed templates")
    if not isinstance(common["model"], str) or not common["model"].strip():
        raise ValueError("A fixed model ID is required")
    safe_base_url(common["base_url"])
    for name in ("request_timeout_seconds", "sql_timeout_seconds"):
        if type(common[name]) not in (int, float) or not math.isfinite(common[name]) or common[name] <= 0:
            raise ValueError("Timeouts must be finite and positive")
    if (type(common["max_rows"]) is not int or not 1 <= common["max_rows"] <= 10000
            or common["token_parameter"] not in {"max_tokens", "max_completion_tokens"}):
        raise ValueError("Invalid SQL or token configuration")
    for key in ("max_output_tokens", "max_prompt_chars"):
        if type(common[key]) is not int or common[key] <= 0:
            raise ValueError("Token/prompt limits must be positive integers")
    if common["temperature"] is not None and (type(common["temperature"]) not in (int, float)
            or not math.isfinite(common["temperature"]) or not 0 <= common["temperature"] <= 2):
        raise ValueError("Invalid temperature")
    prices = config["pricing"]
    if prices["input_per_million"] is not None or prices["output_per_million"] is not None:
        if (not isinstance(prices.get("source"), str) or not prices["source"].strip()
                or not prices.get("verified_at") or prices.get("model") != common["model"]
                or any(type(prices[k]) not in (int, float) or not math.isfinite(prices[k]) or prices[k] < 0
                       for k in ("input_per_million", "output_per_million"))):
            raise ValueError("Cost estimation requires dated, model-specific reliable prices")
    return config


def prepare_dataset(config, cases_path, db_root):
    if file_sha256(cases_path) != config["dataset_sha256"]:
        raise ValueError("Dataset JSON SHA256 does not match the versioned configuration")
    manifest = PROJECT_ROOT / config["manifest"]
    if file_sha256(manifest) != config["manifest_sha256"]:
        raise ValueError("Fixed manifest SHA256 mismatch")
    if json.loads(manifest.read_text(encoding="utf-8-sig")).get("source_revision") != config["dataset_version"]:
        raise ValueError("Dataset revision does not match the pinned manifest")
    all_cases = load_cases(cases_path)
    if len(all_cases) != config["dataset_question_count"]:
        raise ValueError("Dataset question count mismatch")
    cases = select_manifest_cases(all_cases, manifest, config["dataset_sha256"])
    if len(cases) != config["case_count"] or len({c["db_id"] for c in cases}) != config["database_count"]:
        raise ValueError("Manifest case/database count mismatch")
    paths = {c["db_id"]: find_db(db_root, c["db_id"]) for c in cases}
    fingerprints = {db: file_sha256(path) for db, path in paths.items()}
    if fingerprints != config["database_sha256"]:
        raise ValueError("SQLite SHA256 mismatch with pinned database version")
    return cases, paths, fingerprints


def _record(group, run_id, case, common, config, execution):
    mode, linking, repair = GROUPS[group]
    return {"run_id": run_id, "experiment_group": group, "mode": mode, "case_id": case["case_id"],
            "db_id": case["db_id"], "difficulty": case.get("difficulty"), "model_id": common["model"],
            "dataset_version": config["dataset_version"], "dataset_sha256": config["dataset_sha256"],
            "measurement_kind": {"mock": "OFFLINE_MOCK_NOT_MODEL_QUALITY", "dry-run": "DRY_RUN", "live": "REAL_MODEL"}[execution],
            "offline_mock": execution == "mock", "processed": False, "status": "not_run",
            "initial_sql": "", "final_sql": "", "generation_success": False,
            "initial_execution_ok": False, "final_execution_ok": False,
            "error_category": None, "schema_linking_enabled": linking, "repair_enabled": repair,
            "selected_tables": [], "schema_fallback": False, "schema_recall": None,
            "repair_trigger": False, "repair_attempts": 0, "repair_success": None, "repair_execution_recovered": False,
            "model_calls": 0, "prompt_tokens": None, "completion_tokens": None, "model_latency_ms": 0.0,
            "total_latency_ms": None, "execution_error": None, "local_result_match_proxy": None,
            "proxy_status": "not_run", "usage_missing": False, "usage_missing_calls": 0, "estimated_cost": 0.0,
            "request_attempt_ids": [], "node_metrics": {}, "execution_history": [], "trace": []}


def run_suite(config_path, cases_path, db_root, out_dir, *, groups=("A", "B", "C", "D"), execution="dry-run",
              allow_api=False, group_budgets=None, batch_budget=None, mock_scenario="read_only_probe",
              client_factory=None):
    """No resume. Live requires explicit opt-in; Mock factories receive no Gold/case records."""
    if execution not in {"dry-run", "mock", "live"} or not groups or len(set(groups)) != len(groups) or any(g not in GROUPS for g in groups):
        raise ValueError("Invalid execution mode or groups")
    if execution == "live" and not allow_api:
        raise ValueError("Live ablation requires fresh explicit --allow-api authorization")
    if mock_scenario not in {"read_only_probe", "fault_injection_v1"}:
        raise ValueError("Unknown Mock scenario")
    if client_factory is not None and execution != "mock":
        raise ValueError("Injected clients are allowed only in explicit Mock experiments")
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError("Existing experiment directory protected; no automatic resume or overwrite")
    config = load_configuration(config_path)
    common = config["common"]
    cases_path, db_root = Path(cases_path), Path(db_root)
    if out_dir.resolve().is_relative_to(db_root.resolve()):
        raise ValueError("Experiment output must not be inside the SQLite directory")
    cases, paths, before = prepare_dataset(config, cases_path, db_root)
    ids = [c["case_id"] for c in cases]
    theoretical = {g: len(cases) * (1 + GROUPS[g][1] + GROUPS[g][2]) for g in groups}
    caps = {g: config["groups"][g]["max_http_attempts"] for g in groups}
    for g, cap in (group_budgets or {}).items():
        if g not in caps or type(cap) is not int or not 0 <= cap <= caps[g]:
            raise ValueError("Group budgets can only reduce the implemented theoretical limit")
        caps[g] = cap
    ceiling = min(sum(caps.values()), config["batch_max_http_attempts"])
    cap = ceiling if batch_budget is None else batch_budget
    if type(cap) is not int or not 0 <= cap <= ceiling:
        raise ValueError("Batch budget must be within the sum of selected group caps")
    # Atomic directory creation reserves the entire experiment namespace across processes.
    out_dir.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    manifest = {"run_id": run_id, "suite_version": config["suite_version"], "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "python_executable": sys.executable, "execution": execution, "api_authorized": execution == "live" and allow_api,
                "groups": list(groups), "group_budgets": caps, "theoretical_group_caps": theoretical,
                "max_model_calls": cap, "request_audit_version": AUDIT_VERSION, "attempt_log_file": "batch.attempts.jsonl",
                "model": common["model"], "common": common, "response_parser_version": RESPONSE_PARSER_VERSION,
                "case_ids": ids, "sample_metadata": [{k: c.get(k) for k in ("case_id", "db_id", "difficulty")} for c in cases],
                "dataset_version": config["dataset_version"], "dataset_sha256": config["dataset_sha256"],
                "manifest_sha256": config["manifest_sha256"], "database_sha256": before,
                "source_config_path": str(Path(config_path).resolve()), "source_config_sha256": file_sha256(config_path),
                "cases_path": str(cases_path.resolve()), "db_root": str(db_root.resolve()), "pricing": config["pricing"],
                "mock_scenario": mock_scenario if execution == "mock" else None,
                "fault_injection": execution == "mock" and mock_scenario == "fault_injection_v1"}
    write_new_json(out_dir / "batch.config.json", redact(manifest))
    batch = RequestLog(out_dir / "batch.attempts.jsonl", run_id, common["model"], cap)
    evaluator = OfflineEvaluator()
    rows_by_group, summaries, stop_batch, stopped_groups = {}, {}, None, {}
    snapshots = {}
    try:
        for g in groups:
            mode, linking, repair = GROUPS[g]
            scoped = GroupRequestLog(batch, g, caps[g])
            model, tracked, graph, setup_error, stop_group = None, None, None, None, None
            sdk = None
            if execution != "dry-run" and not stop_batch and caps[g] > 0 and batch.reserved < cap:
                try:
                    if execution == "mock":
                        sdk = client_factory(g) if client_factory else MockCompletions(mock_scenario)
                    with offline_dotenv(execution != "live"):
                        model = Model(model=common["model"], base_url=common["base_url"], temperature=common["temperature"],
                            request_timeout=common["request_timeout_seconds"], max_retries=0, max_calls=caps[g],
                            max_output_tokens=common["max_output_tokens"], token_parameter=common["token_parameter"],
                            attempt_log=scoped, client=NS(chat=NS(completions=sdk)) if sdk is not None else None)
                    tracked = TrackedModel(model)
                except Exception:
                    setup_error = "model_configuration_error"
            if mode != "baseline":
                graph = build_schema_graph(tracked, schema_linking=linking, sql_repair=repair, dry_run=execution == "dry-run",
                    sql_timeout=common["sql_timeout_seconds"], max_rows=common["max_rows"], max_prompt_chars=common["max_prompt_chars"])
            rows = []
            try:
                with (out_dir / f"{g}.jsonl").open("x", encoding="utf-8") as stream:
                    for index, case in enumerate(cases):
                        row = _record(g, run_id, case, common, config, execution)
                        planned_fault = ("missing_table" if index % 3 == 1 else "syntax" if index % 3 == 2 else None)
                        row.update(mock_scenario=manifest["mock_scenario"], fault_injection=manifest["fault_injection"],
                                   mock_fault_case=planned_fault if manifest["fault_injection"] else None,
                                   mock_fault_applied=False, mock_client_injected=client_factory is not None)
                        reason = stop_batch or stop_group
                        if execution != "dry-run" and not reason and (caps[g] == 0 or batch.reserved >= cap):
                            reason = "model_budget_exceeded"
                            if batch.reserved >= cap:
                                stop_batch = reason
                            else:
                                stop_group = reason
                        if reason:
                            row.update(error_category=reason, status="not_run", proxy_status="not_run")
                        else:
                            start = time.monotonic()
                            row["processed"] = True
                            result = {}
                            if tracked:
                                tracked.reset()
                            try:
                                if setup_error:
                                    raise RuntimeError(setup_error)
                                snapshot = snapshots.setdefault(case["db_id"], None)
                                if snapshot is None:
                                    snapshot = read_schema(paths[case["db_id"]])
                                    snapshots[case["db_id"]] = snapshot
                                agent = AgentInput.from_case(case, paths[case["db_id"]], mode, no_evidence=not common["evidence"])
                                if tracked:
                                    model.set_case_id(case["case_id"])
                                    if callable(getattr(sdk, "set_context", None)):
                                        sdk.set_context(index, snapshot)
                                # Snapshot cache is evaluator/Mock preparation, not
                                # Agent time; exclude its first-group cold-read bias.
                                start = time.monotonic()
                                if mode == "baseline":
                                    result = run_baseline(agent, tracked, dry_run=execution == "dry-run",
                                        sql_timeout=common["sql_timeout_seconds"], max_rows=common["max_rows"],
                                        max_prompt_chars=common["max_prompt_chars"])
                                else:
                                    result = schema_graph_record(graph.invoke(agent.to_state()))
                                selection = result.get("schema_selection", full_selection(snapshot))
                                schema = result.get("schema_linking", {**schema_metrics(snapshot["full_schema"], snapshot["full_schema"]),
                                          "selection_attempted": False, "selection_valid": None, "fallback": False})
                                row.update(initial_sql=result.get("original_sql", result["generated_sql"]),
                                    final_sql=result["generated_sql"], generation_success=result["generation_success"],
                                    initial_execution_ok=result.get("initial_execution_ok", result["execution_ok"]),
                                    final_execution_ok=result["execution_ok"], status=result["status"],
                                    error_category=result.get("error_type"), execution_error=result.get("execution_error_type"),
                                    output_truncated=result.get("output_truncated"), selected_tables=list(selection["tables"]),
                                    schema_fallback=schema.get("fallback", False), schema_linking=schema,
                                    repair_trigger=result.get("repair_trigger", False), repair_attempts=result.get("repair_attempts", 0),
                                    repair_success=result.get("repair_success"), repair_execution_recovered=result.get("repair_execution_recovered", False),
                                    node_metrics=result.get("node_metrics", {}), trace=result.get("trace", []),
                                    execution_history=result.get("execution_history", []))
                                row["mock_fault_applied"] = bool(row["mock_fault_case"] and result["generation_success"])
                                # Finish measuring Agent before independent Gold evaluation/caching.
                                row["total_latency_ms"] = round((time.monotonic() - start) * 1000, 3)
                                if execution == "dry-run":
                                    row.update(initial_execution_ok=None, proxy_status="dry_run")
                                else:
                                    row.update(evaluator.evaluate(case, paths[case["db_id"]], snapshot, result, selection))
                                    if row["repair_execution_recovered"]:
                                        row["repair_success"] = row["local_result_match_proxy"]
                            except Exception as exc:
                                category = setup_error or error_info(exc, "data")[0]
                                row.update(error_category=category, status="failed", proxy_status="pipeline_failure")
                            if row["total_latency_ms"] is None:
                                row["total_latency_ms"] = round((time.monotonic() - start) * 1000, 3)
                            if tracked:
                                row.update(tracked.metrics)
                            row["usage_missing_calls"] = row["model_calls"] - row.get("usage_reported_calls", 0)
                            row["usage_missing"] = row["usage_missing_calls"] > 0
                            if execution == "live":
                                row["estimated_cost"] = estimated_cost(row, config["pricing"]["input_per_million"], config["pricing"]["output_per_million"])
                            elif row["model_calls"]:
                                row["estimated_cost"] = None
                            category = row["error_category"]
                            if category == "model_budget_exceeded":
                                stop_group = category
                                if batch.reserved >= cap:
                                    stop_batch = category
                            elif category and category not in ISOLATED_CASE_ERRORS:
                                stop_batch = category
                        row = redact(row)
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                        rows.append(row)
                rows_by_group[g] = rows
                if stop_group:
                    stopped_groups[g] = stop_group
                audit = inspect_request_log(batch.path, run_id, cap)
                group_audit = {"batch_log": batch.path.name, "run_id": run_id, "group": g, "group_cap": caps[g],
                               "attempts": [a for a in audit["attempts"] if str(a["case_id"]).startswith(g + ":")],
                               "automatic_resume_allowed": False}
                write_new_json(out_dir / f"{g}.audit.json", group_audit)
                summaries[g] = summarize_group(rows, ids)
                write_new_json(out_dir / f"{g}.summary.json", summaries[g])
            finally:
                if model:
                    try:
                        model.close()
                    except Exception:
                        stop_batch = "model_cleanup_error"
    finally:
        batch.close()
    after = {db: file_sha256(path) for db, path in paths.items()}
    manifest_unchanged = file_sha256(PROJECT_ROOT / config["manifest"]) == config["manifest_sha256"]
    dataset_unchanged = file_sha256(cases_path) == config["dataset_sha256"]
    if before != after or not manifest_unchanged or not dataset_unchanged:
        raise RuntimeError("Pinned dataset bytes changed; experiment cannot be accepted")
    summary = {"run_id": run_id, "suite_version": config["suite_version"], "execution": execution,
               "measurement_kind": {"mock": "OFFLINE_MOCK_NOT_MODEL_QUALITY", "dry-run": "DRY_RUN", "live": "REAL_MODEL"}[execution],
               "planned_cases_per_group": len(cases),
               "group_summaries": summaries, "paired_comparisons": paired_comparisons(rows_by_group),
               "request_audit": inspect_request_log(batch.path, run_id, cap), "group_budgets": caps, "batch_budget": cap,
               "model_calls": sum(s["model_calls"] for s in summaries.values()), "stop_reason": stop_batch,
               "stopped_groups": stopped_groups,
               "complete": not stop_batch and not stopped_groups and all(r["processed"] for rows in rows_by_group.values() for r in rows),
               "sqlite_sha256_unchanged": before == after, "database_sha256_after": after,
               "dataset_sha256_unchanged": dataset_unchanged, "manifest_sha256_unchanged": manifest_unchanged,
               "remote_api_requests": 0 if execution != "live" else "see_request_audit_not_delivery_proof",
               "official_ex": None, "historical_v3_used_as_experimental_rows": False}
    write_new_json(out_dir / "batch.summary.json", redact(summary))
    return summary
