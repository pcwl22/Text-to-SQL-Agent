"""Bounded baseline experiments; real API calls require an explicit opt-in."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

from bird_starter.baseline import error_info, run_baseline
from bird_starter.config import load_project_env
from bird_starter.data import AgentInput, dataset_paths, load_cases, find_db
from bird_starter.model import Model, ModelFailure, TrackedModel, redact, safe_base_url, RESPONSE_PARSER_VERSION
from bird_starter.prompts import PROMPT_VERSION
from bird_starter.reporting import estimated_cost, summarize
from bird_starter.request_audit import AUDIT_VERSION, RequestLog, inspect_experiment, write_new_json
from bird_starter.workflow import build_graph, build_schema_graph
from bird_starter.schema_workflow import schema_graph_record
from bird_starter.schema_linking import LINK_PROMPT_VERSION
from bird_starter.sql_repair import REPAIR_PROMPT_VERSION
from bird_starter.repair_reporting import repair_summary
from bird_starter.experiment_sets import select_manifest_cases
from bird_starter.evaluation import result_match_proxy
from scripts.validate_dataset import EXPECTED_GIT_BLOB, SOURCE_REVISION, file_fingerprints

ISOLATED_CASE_ERRORS = frozenset({
    "empty_response", "model_output_truncated", "sql_syntax_error", "sql_execution_error",
    "sql_schema_error", "sql_timeout", "sql_safety_error", "sql_resource_error",
})
CONTROLLED_GRAPH_MODES = frozenset({"schema_only", "repair_only", "full"})


def _stop_for_error(category, policy):
    if policy == "isolate":
        # Explicit allowlist: unknown, data, provider and audit failures stop the batch.
        return bool(category) and category not in ISOLATED_CASE_ERRORS
    return category.startswith("model_") or category == "empty_response"


def _positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Expected a finite positive number")
    return number


def _temperature(value):
    if value.lower() in {"omit", "none"}:
        return None
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 2:
        raise argparse.ArgumentTypeError("temperature must be 0..2 or omit")
    return number


def _graph_record(output):
    sql, error = output.get("sql", ""), output.get("error", "")
    category, message = None, None
    if error:
        if "SQLSafetyError" in error:
            category, message = "sql_safety_error", error
        elif "ParseError" in error:
            category, message = "sql_syntax_error", "SQL AST parsing failed"
        else:
            category, message = error_info(sqlite3.OperationalError(error), "execute")
    return {"generated_sql": sql, "generation_success": bool(sql), "execution_ok": not bool(error),
            "execution_error_type": category, "execution_error_message": message,
            "execution_time_ms": output.get("elapsed_ms"), "output_truncated": not output.get("complete", False),
            "attempts": output.get("attempts", 0), "error_type": category, "error_message": message,
            "status": "failed" if error else "execution_succeeded", "dry_run": False}


def main(argv=None):
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=None, help="BIRD_CASES_PATH or project default")
    parser.add_argument("--db-root", default=None, help="BIRD_DB_ROOT or project default")
    parser.add_argument("--mode", choices=("baseline", "graph", "schema_only", "repair_only", "full"), required=True)
    parser.add_argument("--schema-linking", choices=("on", "off"), default=None,
                        help="Controlled graph switch; defaults: schema_only/full on, repair_only off")
    parser.add_argument("--sql-repair", choices=("on", "off"), default=None,
                        help="Controlled graphs only; default off except explicit repair_only/full modes; at most once")
    parser.add_argument("--case-manifest", default=None, help="Fixed metadata manifest; dataset SHA256 must match")
    parser.add_argument("--difficulty", default=None)
    parser.add_argument("--limit", type=int, default=10, help="0 writes an empty run with zero API calls")
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument("--max-repairs", type=int, default=1, help="Legacy graph only; ignored by baseline")
    parser.add_argument("--allow-api", action="store_true", help="Explicit permission to spend API credits")
    parser.add_argument("--dry-run", action="store_true", help="Read schemas only; never create a model client")
    parser.add_argument("--overwrite", action="store_true", help="Deprecated: experiments can never be overwritten")
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--case-error-policy", choices=("legacy", "isolate"), default="legacy",
                        help="isolate: continue known per-case failures; baseline only, requires retries=0")
    parser.add_argument("--experiment-version", default=None, help="Recorded configuration label, not authorization")
    parser.add_argument("--model", default=None, help="OPENAI_MODEL if omitted")
    parser.add_argument("--base-url", default=None, help="OPENAI_BASE_URL if omitted; never a key")
    parser.add_argument("--temperature", type=_temperature, default=0.0, help="0 by default; omit if unsupported")
    parser.add_argument("--request-timeout", type=_positive, default=30.0)
    parser.add_argument("--request-retries", type=int, default=1)
    parser.add_argument("--max-model-calls", type=int, default=10, help="Run-wide HTTP attempt cap, including retries")
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--token-parameter", choices=("max_tokens", "max_completion_tokens"), default="max_completion_tokens")
    parser.add_argument("--sql-timeout", type=_positive, default=8.0)
    parser.add_argument("--max-rows", type=int, default=1000)
    parser.add_argument("--max-prompt-chars", type=int, default=200000)
    parser.add_argument("--input-price-per-million", type=float, default=None)
    parser.add_argument("--output-price-per-million", type=float, default=None)
    parser.add_argument("--currency", default="USD")
    args = parser.parse_args(argv)
    if args.overwrite:
        parser.error("Experiment overwrite is prohibited; preserve files and choose a fresh output after new authorization")
    if args.limit < 0 or args.max_repairs < 0:
        parser.error("limit and max-repairs must be non-negative")
    if args.case_error_policy == "isolate" and (args.mode not in {"baseline", *CONTROLLED_GRAPH_MODES} or args.request_retries != 0):
        parser.error("isolate requires baseline/schema_only and --request-retries 0; no replay is allowed")
    if (args.schema_linking is not None or args.sql_repair is not None) and args.mode not in CONTROLLED_GRAPH_MODES:
        parser.error("Schema/repair switches require schema_only, repair_only or full")
    if args.mode in CONTROLLED_GRAPH_MODES and args.request_retries != 0:
        parser.error("Controlled graphs require --request-retries 0; no automatic retries")
    link_enabled = (args.schema_linking == "on" if args.schema_linking is not None else args.mode != "repair_only")
    repair_enabled = (args.sql_repair == "on" if args.sql_repair is not None else args.mode in {"repair_only", "full"})
    # New repair strategies always stop on unknown/system failures, regardless
    # of the legacy compatibility policy. Baseline/schema-only defaults stay intact.
    if args.mode in {"repair_only", "full"} or (args.mode in CONTROLLED_GRAPH_MODES and repair_enabled):
        args.case_error_policy = "isolate"
    if (not 0 <= args.request_retries <= 3 or args.max_model_calls <= 0 or args.max_output_tokens <= 0
            or not 1 <= args.max_rows <= 10000 or args.max_prompt_chars <= 0):
        parser.error("Invalid retry, call, token, row or prompt limit")
    prices = (args.input_price_per_million, args.output_price_per_million)
    if any(p is not None for p in prices) and not all(p is not None and math.isfinite(p) and p >= 0 for p in prices):
        parser.error("Both prices must be configured as finite non-negative values")
    if args.limit and not args.dry_run and not args.allow_api:
        parser.error("Real model use requires --allow-api; use --dry-run for offline preflight")
    try:
        base_url = safe_base_url(args.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
        cases_path, db_root = dataset_paths(args.cases, args.db_root)
        cases = load_cases(cases_path, args.difficulty, args.limit)
        if args.case_manifest:
            cases = select_manifest_cases(load_cases(cases_path), args.case_manifest,
                                          file_fingerprints(cases_path)["sha256"])
            if args.difficulty:
                cases = [c for c in cases if c.get("difficulty", "").lower() == args.difficulty.lower()]
            cases = cases[:args.limit]
    except (OSError, ValueError) as exc:
        parser.error(redact(str(exc)))
    out = Path(args.out)
    config_path, summary_path = out.with_suffix(".config.json"), out.with_suffix(".summary.json")
    log_path = out.with_suffix(".attempts.jsonl")
    output_paths = (out, config_path, summary_path, log_path)
    if len({p.resolve() for p in output_paths}) != 4:
        parser.error("Choose an output name distinct from its manifest, summary and request log")
    if any(p.resolve() == cases_path.resolve() or p.resolve().is_relative_to(db_root.resolve())
           for p in output_paths):
        parser.error("Outputs must not overwrite the question file or files inside the database root")
    if any(p.exists() for p in output_paths):
        recovery = "identity/budget UNKNOWN"
        if config_path.exists():
            try:
                old = inspect_experiment(config_path)
                recovery = (f"run_id={old['run_id']}, state={old['audit_state']}, "
                            f"budget_reserved={old['budget_reserved']}; no automatic resume")
            except ValueError:
                pass
        parser.error(f"Existing experiment protected: {recovery}. Inspect offline; new authorization and fresh output required")
    out.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = file_fingerprints(cases_path)
    run_id = uuid.uuid4().hex
    model_options = dict(model=args.model, base_url=base_url, temperature=args.temperature,
                         request_timeout=args.request_timeout, max_retries=args.request_retries,
                         max_calls=args.max_model_calls, max_output_tokens=args.max_output_tokens,
                         token_parameter=args.token_parameter)
    config = {
        "run_id": run_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable, "python_version": sys.version.split()[0],
        "mode": args.mode, "dry_run": args.dry_run, "api_authorized": args.allow_api,
        "experiment_version": args.experiment_version, "case_error_policy": args.case_error_policy,
        "continuable_case_errors": sorted(ISOLATED_CASE_ERRORS) if args.case_error_policy == "isolate" else None,
        "concurrency": 1,
        "cases_path": str(cases_path.resolve()), "db_root": str(db_root.resolve()),
        "dataset_sha256": fingerprint["sha256"],
        "dataset_version": SOURCE_REVISION if fingerprint["git_blob_sha1"] == EXPECTED_GIT_BLOB else None,
        "case_ids": [case["case_id"] for case in cases], "difficulty": args.difficulty,
        "evidence": not args.no_evidence, "prompt_version": PROMPT_VERSION,
        "model": args.model or os.environ.get("OPENAI_MODEL"), "base_url": base_url,
        "temperature": args.temperature, "request_timeout_seconds": args.request_timeout,
        "request_retries": args.request_retries, "sdk_retries": 0, "n": 1,
        "max_model_calls": args.max_model_calls, "max_output_tokens": args.max_output_tokens,
        "token_parameter": args.token_parameter, "sql_timeout_seconds": args.sql_timeout,
        "max_rows": args.max_rows, "max_prompt_chars": args.max_prompt_chars,
        "max_repairs": (int(repair_enabled) if args.mode in CONTROLLED_GRAPH_MODES else
                        0 if args.mode == "baseline" else args.max_repairs),
        "local_proxy_enabled": not args.no_proxy, "official_ex": False,
        "stop_on_model_error": args.case_error_policy == "legacy",
        "stop_on_system_error": args.case_error_policy == "isolate",
        "response_parser_version": RESPONSE_PARSER_VERSION,
        "http_redirects": False, "request_audit_version": AUDIT_VERSION,
        "attempt_log_file": log_path.name,
        "prices_per_million": {"input": prices[0], "output": prices[1], "currency": args.currency},
    }
    if args.mode in CONTROLLED_GRAPH_MODES:
        config.update(schema_linking=link_enabled, sql_repair=repair_enabled,
                      repair_prompt_version=REPAIR_PROMPT_VERSION if repair_enabled else None,
                      schema_link_prompt_version=LINK_PROMPT_VERSION,
                      token_estimate_method="ceil_utf8_bytes_div_4_not_provider_usage")
    if args.case_manifest:
        config.update(case_manifest_path=str(Path(args.case_manifest).resolve()),
                      case_manifest_sha256=file_fingerprints(Path(args.case_manifest))["sha256"])
    # The exclusive, durable manifest is a permanent per-output reservation across
    # processes. A crashed owner's files are never automatically removed/reused.
    try:
        write_new_json(config_path, redact(config))
    except FileExistsError:
        parser.error("Experiment reserved by another process; preserve existing files and do not retry")
    attempt_log = RequestLog(log_path, run_id, config["model"], args.max_model_calls)
    model, tracked, graph, setup_error = None, None, None, None
    if cases and not args.dry_run:
        try:
            model = Model(**model_options, attempt_log=attempt_log)
            tracked = TrackedModel(model)
            if args.mode == "graph":
                graph = build_graph(tracked, max_repairs=args.max_repairs,
                                    sql_timeout=args.sql_timeout, max_rows=args.max_rows)
        except Exception as exc:
            setup_error = ModelFailure(getattr(exc, "category", "model_configuration_error"))
    if args.mode in CONTROLLED_GRAPH_MODES:
        graph = build_schema_graph(tracked, schema_linking=link_enabled, sql_repair=repair_enabled, dry_run=args.dry_run,
                                   sql_timeout=args.sql_timeout, max_rows=args.max_rows,
                                   max_prompt_chars=args.max_prompt_chars)
    records = []
    stop_reason = None
    try:
        with out.open("x", encoding="utf-8") as stream:
            for case in cases:
                if tracked:
                    tracked.reset()
                    if callable(getattr(model, "set_case_id", None)):
                        model.set_case_id(case["case_id"])
                start = time.monotonic()
                record = {
                    "run_id": run_id, "config_file": config_path.name, "case_id": case["case_id"],
                    "question_id": case.get("question_id"), "db_id": case["db_id"],
                    "difficulty": case.get("difficulty"), "mode": args.mode, "model": config["model"],
                    "generated_sql": "", "generation_success": False, "execution_ok": False,
                    "execution_error_type": None, "execution_error_message": None, "execution_time_ms": None,
                    "output_truncated": None, "attempts": 0, "error_type": None, "error_message": None,
                    "status": "failed", "dry_run": args.dry_run, "local_result_match_proxy": None,
                }
                stage = "data"
                try:
                    db_path = find_db(db_root, case["db_id"])
                    agent_input = AgentInput.from_case(case, db_path, args.mode, no_evidence=args.no_evidence)
                    if setup_error:
                        raise setup_error
                    if args.mode in CONTROLLED_GRAPH_MODES:
                        record.update(schema_graph_record(graph.invoke(agent_input.to_state())))
                    elif args.dry_run or args.mode == "baseline":
                        record.update(run_baseline(
                            agent_input, tracked, sql_timeout=args.sql_timeout, max_rows=args.max_rows,
                            max_prompt_chars=args.max_prompt_chars, dry_run=args.dry_run))
                    else:
                        stage = "model"
                        record.update(_graph_record(graph.invoke(agent_input.to_state())))
                except Exception as exc:
                    category, message = error_info(exc, stage)
                    record.update(error_type=category, error_message=message)
                record["latency_ms"] = round((time.monotonic() - start) * 1000, 3)
                record.update(tracked.metrics if tracked else {
                    "model_calls": 0, "model_latency_ms": 0.0, "prompt_tokens": None,
                    "completion_tokens": None, "usage_reported_calls": 0, "response_model": None})
                record["model_latency_ms"] = round(record["model_latency_ms"], 3)
                # Gold is consumed only by the offline evaluator; never repair from this result.
                if record["execution_ok"] and not record["output_truncated"] and not args.no_proxy:
                    record["local_result_match_proxy"] = result_match_proxy(
                        str(db_path), record["generated_sql"], case["SQL"])
                # Evaluator-only annotation after graph END; cannot route another repair.
                if record.get("repair_execution_recovered") and record["local_result_match_proxy"] is not None:
                    record["repair_success"] = record["local_result_match_proxy"] is True
                    record["repair_success_basis"] = "local_proxy_not_official_EX"
                record["estimated_cost"] = estimated_cost(record, *prices)
                # Compatibility aliases for existing output consumers.
                record["sql"] = record["generated_sql"]
                record["error"] = record["error_message"] or ""
                if args.case_error_policy == "isolate":
                    category = record["error_type"] or ""
                    record["failure_scope"] = ("system" if _stop_for_error(category, "isolate")
                                               else "case" if category else None)
                clean = redact(record)
                stream.write(json.dumps(clean, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                records.append(clean)
                print(redact(f"[{len(records)}/{len(cases)}] {case['case_id']} status={record['status']} "
                             f"error={record['error_type']}"), flush=True)
                error_type = record["error_type"] or ""
                if _stop_for_error(error_type, "isolate" if args.mode in {"repair_only", "full"} or
                                   (args.mode in CONTROLLED_GRAPH_MODES and repair_enabled) else args.case_error_policy):
                    stop_reason = error_type
                    break
    finally:
        attempt_log.close()
        if model and callable(getattr(model, "close", None)):
            try:
                model.close()
            except Exception:
                pass  # Preserve completed records/summary if client cleanup fails.
    summary = summarize(records)
    if args.mode in CONTROLLED_GRAPH_MODES:
        summary["sql_repair"] = repair_summary(records)
    summary.update(run_id=run_id, config_file=config_path.name, currency=args.currency,
                   experiment_version=args.experiment_version, case_error_policy=args.case_error_policy,
                   planned_cases=len(cases), unprocessed_case_ids=[c["case_id"] for c in cases[len(records):]],
                   stopped_early=len(records) < len(cases), stop_reason=stop_reason,
                   request_audit=inspect_experiment(config_path))
    write_new_json(summary_path, redact(summary))
    print(redact(f"Saved {len(records)} records -> {out}; summary -> {summary_path}. "
                 "Execution success is NOT accuracy; proxy is NOT official BIRD EX."))
    return summary


if __name__ == "__main__":
    summary = main()
    if summary["stop_reason"]:
        raise SystemExit(1)
    if summary["dry_run_cases"] and summary["preflight_successes"] < summary["dry_run_cases"]:
        raise SystemExit(1)
