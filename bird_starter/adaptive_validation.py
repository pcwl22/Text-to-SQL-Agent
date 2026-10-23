"""Dedicated P1B B/E runner. Default dry-run; no evaluator or Gold at runtime."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace as NS
import uuid

from .ablation_mock import MockCompletions
from .adaptive_validation_budget import BudgetLedger, consume_authorization, verify_live_authorization, RECORD_ONLY
from .adaptive_validation_config import load_frozen, read_json
from .adaptive_validation_reporting import aggregate
from .data import AgentInput
from .experiment_review import reserve_private_output
from .experiment_sets import file_sha256
from .model import Model, ModelFailure, redact
from .request_audit import RequestLog, inspect_request_log, write_new_json
from .schema_linking import read_schema
from .schema_routing import AdaptiveSchemaGate
from .schema_workflow import build_schema_graph, schema_graph_record


@contextmanager
def isolated_runtime(offline):
    """No production dotenv in any mode. Offline modes cannot open a socket."""
    values = {k: os.environ.get(k) for k in ("PYTHON_DOTENV_DISABLED", "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")}
    os.environ.update(PYTHON_DOTENV_DISABLED="1", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    previous = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def deny(*_, **__):
        raise AssertionError("P1B offline runtime blocks all network")
    if offline:
        socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    try:
        yield
    finally:
        if offline:
            socket.socket.connect, socket.socket.connect_ex, socket.create_connection = previous
        for key, old in values.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


class NodeModel:
    def __init__(self, adapter, ledger, common, execution):
        self.delegate, self.ledger, self.common, self.execution = adapter, ledger, common, execution
        self.group, self.identity = None, None

    def ask(self, system, user):
        node = "linking" if system.startswith("Select relevant SQLite") else "generation"
        self.ledger.context = (self.group, self.identity, node)
        self.delegate.last_call = {"model_calls": 0, "request_attempt_ids": [], "prompt_tokens": None,
                                   "completion_tokens": None, "usage_reported_calls": 0}
        self.ledger.quote(system, user, self.common["max_output_tokens"])
        self.delegate.set_case_id(self.identity)
        try:
            answer = self.delegate.ask(system, user)
            if self.execution == "live" and self.delegate.last_call.get("response_model") != self.common["model"]:
                self.ledger.stop_reason = "model_identity_unverified"
            if self.ledger.stop_reason:
                raise ModelFailure(self.ledger.stop_reason)
            return answer
        except BaseException as exc:
            category = getattr(exc, "category", "model_request_failed")
            continued_content_failure = (self.ledger.record_only and self.ledger.last_content_failure == category
                                         and self.delegate.last_call.get("response_model") == self.common["model"])
            if not continued_content_failure:
                self.ledger.stop_reason = self.ledger.stop_reason or category
            raise


def run_validation(frozen_dir, cases_path, db_root, out_dir, *, execution="dry-run", allow_api=False,
                   authorization=None, client_factory=None, max_attempts=None, project_env=False):
    if execution not in ("dry-run", "mock", "live") or (allow_api and execution != "live"):
        raise ValueError("Invalid execution/API mode")
    if execution == "live" and (not allow_api or not authorization or client_factory is not None):
        raise ValueError("Live requires separate --live --allow-api and one-time authorization")
    if client_factory is not None and execution != "mock":
        raise ValueError("Injected SDKs are Mock-only")
    if project_env and execution != "live":
        raise ValueError("Project credential loading is Live-only")
    with isolated_runtime(execution != "live"):
        return _run(frozen_dir, cases_path, db_root, out_dir, execution, authorization, client_factory, max_attempts, project_env)


def _run(frozen_dir, cases_path, db_root, out_dir, execution, authorization, client_factory, max_attempts, project_env):
    config, manifest, projected, paths = load_frozen(frozen_dir, cases_path, db_root)
    common = config["common"]
    auth = verify_live_authorization(authorization, config, frozen_dir) if execution == "live" else None
    record_only = bool(auth and auth.get("cost_policy") == RECORD_ONLY)
    if record_only:
        from .adaptive_validation_connection import verify_connection_receipt
        verify_connection_receipt(auth, config)
    cap = auth["max_attempts"] if auth else config["max_attempts"]
    if max_attempts is not None:
        if type(max_attempts) is not int or not 0 <= max_attempts <= cap:
            raise ValueError("Attempt override may only reduce the frozen/authorized cap")
        cap = max_attempts
    if execution == "live":
        from .adaptive_validation_connection import credentials_ready
        credentials_ready(common, project_env=project_env)
    if Path(out_dir).resolve().is_relative_to(Path(db_root).resolve()):
        raise ValueError("Outputs must not be inside database root")
    output = reserve_private_output(out_dir)
    run_id = uuid.uuid4().hex
    if auth:
        consume_authorization(auth, run_id)
        write_new_json(output / "authorization.json", auth)
    write_new_json(output / "frozen_manifest.json", manifest)
    batch_config = {"run_id": run_id, "version": config["version"], "execution": execution,
                    "max_model_calls": cap, "attempt_log_file": "batch.attempts.jsonl", "request_audit_version": 1,
                    "frozen_config_sha256": file_sha256(Path(frozen_dir) / "config.json"),
                    "frozen_manifest_sha256": config["manifest_sha256"], "config": config,
                    "schedule": manifest["schedule"], "automatic_resume_allowed": False}
    write_new_json(output / "batch.config.json", batch_config)
    batch = RequestLog(output / "batch.attempts.jsonl", run_id, common["model"], cap)
    ledger = BudgetLedger(batch, pricing=config["pricing"] if auth else None, max_usd=auth["max_usd"] if auth else None,
                          cost_path=output / "cost.attempts.jsonl", record_only=record_only)
    rows = {g: [{"run_id": run_id, "experiment_group": g, **meta, "processed": False,
                 "status": "not_run", "error_type": None, "model_calls": 0, "request_attempt_ids": [],
                 "node_metrics": {}, "total_latency_ms": None, "generated_sql": "",
                 "generation_success": False, "execution_ok": False,
                 "measurement_kind": {"live": "REAL_MODEL", "mock": "OFFLINE_MOCK_NOT_MODEL_QUALITY", "dry-run": "DRY_RUN"}[execution]}
                for meta in manifest["cases"]] for g in ("B", "E")}
    models, graphs, sdks = {}, {}, {}
    try:
        for g in ("B", "E"):
            if execution == "dry-run":
                adapter = NS(ask=lambda *_: (_ for _ in ()).throw(AssertionError("Dry-run model call")))
            else:
                sdk = (client_factory(g) if client_factory else MockCompletions("read_only_probe")) if execution == "mock" else None
                sdks[g] = sdk
                adapter = Model(model=common["model"], base_url=common["base_url"], temperature=0,
                    request_timeout=common["request_timeout_seconds"], max_retries=0, max_calls=cap,
                    max_output_tokens=2048, token_parameter=common["token_parameter"], attempt_log=ledger,
                    client=NS(chat=NS(completions=sdk)) if sdk is not None else None)
            wrapper = NodeModel(adapter, ledger, common, execution)
            wrapper.group = g
            models[g] = wrapper
            graphs[g] = build_schema_graph(wrapper, schema_linking=True, sql_repair=False,
                adaptive_linking=AdaptiveSchemaGate(g == "E", 1014 if g == "E" else None), dry_run=execution == "dry-run",
                sql_timeout=common["sql_timeout_seconds"], max_rows=common["max_rows"], max_prompt_chars=common["max_prompt_chars"])
        lookup = {g: {r["case_id"]: r for r in rows[g]} for g in rows}
        for item in manifest["schedule"]:
            group, identity = item["group"], item["case_id"]
            row = lookup[group][identity]
            if ledger.stop_reason or (execution != "dry-run" and batch.reserved >= cap):
                ledger.stop_reason = ledger.stop_reason or "model_budget_exceeded"
                row.update(status="not_run_batch_stopped", error_type=ledger.stop_reason)
                continue
            model = models[group]
            model.identity = identity
            before = batch.reserved
            if execution == "mock":
                sdks[group].set_context(0, read_schema(paths[row["db_id"]]))
            started = time.monotonic()
            # Only the AgentInput allowlist enters the graph. No peer results or case IDs.
            agent = AgentInput.from_case(projected[identity], paths[row["db_id"]], "schema_only", no_evidence=not common["evidence"])
            result = graphs[group].invoke(agent.to_state())
            row.update(schema_graph_record(result), processed=True,
                       total_latency_ms=round((time.monotonic() - started) * 1000, 3), model_calls=batch.reserved - before)
            row["request_attempt_ids"] = [a["attempt_id"] for a in inspect_request_log(batch.path, run_id, cap)["attempts"]
                                           if a["case_id"].startswith(f"{group}:{identity}:")]
            if result.get("error_type") == "sql_safety_error":
                ledger.stop_reason = "sql_safety_rejection"
            # Persist completed case before starting its paired peer.
            with (output / "execution_order.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"group": group, "case_id": identity, "status": row["status"]}) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with (output / "completed_cases.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(redact(row), ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    finally:
        for model in models.values():
            close = getattr(model.delegate, "close", None)
            if close:
                close()
        batch.close()
        ledger.close()
        for group in rows:
            with (output / f"{group}.jsonl").open("x", encoding="utf-8") as stream:
                for row in rows[group]:
                    stream.write(json.dumps(redact(row), ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    audit = inspect_request_log(batch.path, run_id, cap)
    if audit["audit_state"] != "VALID":
        raise ValueError("Audit indeterminate; preserve outputs; never resume")
    if any(file_sha256(paths[db]) != digest for db, digest in config["database_sha256"].items()):
        raise ValueError("Original database hash changed")
    groups = aggregate(rows, audit["attempts"], execution, config["pricing"] if auth else None)
    planned = {"B": 20, "E": sum(1 + int(manifest["schema_distribution"][r["db_id"]]["routing_decision"] == "USE_LINKING")
                                 for r in manifest["cases"])}
    report = {"run_id": run_id, "execution": execution, "planned_cases_per_group": 10,
              "planned_requests": planned, "planned_total_requests": sum(planned.values()),
              "max_request_reservations": cap, "reserved_attempts": audit["prepared_attempts"],
              "real_model_request_reservations": audit["prepared_attempts"] if execution == "live" else 0,
              "groups": groups, "stopped_reason": ledger.stop_reason, "audit": audit,
              "database_hashes_unchanged": True, "official_ex": "NOT_EVALUATED",
              "live_authorized": execution == "live", "pricing_status": config["pricing"]["status"],
              "live_readiness": "STOP_REQUIRES_SEPARATE_AUTHORIZATION_AND_VERIFIED_BILLING" if not auth else "ONE_BATCH_CONSUMED",
              "artifacts_sha256": {p.name: file_sha256(p) for p in output.iterdir() if p.is_file()}}
    write_new_json(output / "preflight.json", report)
    return report
