"""Read-only experiment identity/audit checks. No Gold, Agent or SDK execution."""
import json
from pathlib import Path

from .ablation import GROUPS, PROJECT_ROOT, _record, load_configuration
from .experiment_sets import file_sha256
from .request_audit import inspect_experiment

PRIVATE_OUTPUT_ROOT = PROJECT_ROOT / "outputs"


def reserve_private_output(path):
    path = Path(path).resolve()
    if not path.is_relative_to(PRIVATE_OUTPUT_ROOT.resolve()) or path == PRIVATE_OUTPUT_ROOT.resolve():
        raise ValueError("Evaluation exports/reports must remain under the private ignored outputs directory")
    path.mkdir(parents=True, exist_ok=False)
    return path


def expected_metadata(suite_path):
    config = load_configuration(suite_path)
    manifest = PROJECT_ROOT / config["manifest"]
    if file_sha256(manifest) != config["manifest_sha256"]:
        raise ValueError("Pinned manifest changed")
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    rows = data["cases"]
    if (len(rows) != config["case_count"] or len({r["case_id"] for r in rows}) != len(rows)
            or len({r["db_id"] for r in rows}) != config["database_count"]
            or data["source_revision"] != config["dataset_version"]):
        raise ValueError("Invalid manifest identity/version")
    return config, rows


def read_group(run_dir, group, suite_path):
    """Pad missing results, but reject duplicate/foreign/torn records and forged IDs."""
    if group not in GROUPS:
        raise ValueError("Unknown experiment group")
    config, metadata = expected_metadata(suite_path)
    folder = Path(run_dir)
    batch = json.loads((folder / "batch.config.json").read_text(encoding="utf-8-sig"))
    if (batch["source_config_sha256"] != file_sha256(suite_path) or batch["common"] != config["common"]
            or batch["model"] != config["common"]["model"]
            or batch["dataset_sha256"] != config["dataset_sha256"] or batch["dataset_version"] != config["dataset_version"]
            or batch["database_sha256"] != config["database_sha256"]
            or batch["manifest_sha256"] != config["manifest_sha256"] or batch["sample_metadata"] != metadata
            or batch["case_ids"] != [r["case_id"] for r in metadata] or group not in batch["groups"]
            or batch["execution"] not in {"mock", "dry-run", "live"}):
        raise ValueError("Run configuration differs from the pinned cohort/protocol")
    if not 0 <= batch["group_budgets"][group] <= config["groups"][group]["max_http_attempts"]:
        raise ValueError("Group budget is inconsistent")
    if batch["max_model_calls"] > config["batch_max_http_attempts"]:
        raise ValueError("Batch budget is inconsistent")
    audit = inspect_experiment(folder / "batch.config.json")
    header = json.loads((folder / "batch.attempts.jsonl").read_text(encoding="utf-8").splitlines()[0])
    if header.get("model_id") != config["common"]["model"]:
        raise ValueError("Audit model does not match the experiment configuration")
    indexed = {r["case_id"]: r for r in metadata}
    events = {a["attempt_id"]: a for a in audit.get("attempts", [])}
    known, consumed = {}, set()
    result_path = folder / f"{group}.jsonl"
    if result_path.exists():
        with result_path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError("Torn result JSONL; preserve and reconcile before evaluation")
                row = json.loads(line)
                identity = indexed.get(row["case_id"])
                if identity is None or row["case_id"] in known:
                    raise ValueError("Duplicate or out-of-cohort result")
                if any(key in row for key in ("SQL", "gold_sql", "prompt", "messages", "api_key", "authorization")):
                    raise ValueError("Unexpected sensitive/evaluation input in result record")
                template = _record(group, batch["run_id"], identity, config["common"], config, batch["execution"])
                keys = ("run_id", "experiment_group", "mode", "case_id", "db_id", "difficulty", "model_id",
                        "dataset_version", "dataset_sha256", "measurement_kind", "schema_linking_enabled", "repair_enabled")
                if any(type(row.get(k)) is not type(template[k]) or row.get(k) != template[k] for k in keys):
                    raise ValueError("Result identity, features or measurement kind mismatch")
                ids = row.get("request_attempt_ids", [])
                calls = row["model_calls"]
                ceiling = 1 + GROUPS[group][1] + GROUPS[group][2]
                if type(calls) is not int or not 0 <= calls <= ceiling or not isinstance(ids, list) or len(ids) != calls:
                    raise ValueError("Result request accounting mismatch")
                repairs = row["repair_attempts"]
                nodes = row.get("node_metrics", {})
                if (type(repairs) is not int or not 0 <= repairs <= int(GROUPS[group][2])
                        or nodes.get("repair", {}).get("model_calls", 0) > int(GROUPS[group][2])
                        or nodes.get("schema_linking", {}).get("model_calls", 0) > int(GROUPS[group][1])):
                    raise ValueError("Result workflow exceeds the configured feature/call limit")
                for attempt_id in ids:
                    event = events.get(attempt_id)
                    if (event is None or attempt_id in consumed or event["case_id"] != f"{group}:{row['case_id']}"):
                        raise ValueError("Result attempt ID is missing, duplicated or belongs to another case")
                    consumed.add(attempt_id)
                if type(row["usage_missing_calls"]) is not int or not 0 <= row["usage_missing_calls"] <= calls:
                    raise ValueError("Invalid usage missing count")
                known[row["case_id"]] = row
    aligned, missing = [], []
    for identity in metadata:
        row = known.get(identity["case_id"])
        if row is None:
            missing.append(identity["case_id"])
            row = _record(group, batch["run_id"], identity, config["common"], config, batch["execution"])
            row.update(error_category="missing_result", proxy_status="missing_result", status="not_run")
        aligned.append(row)
    group_events = [a for a in events.values() if str(a["case_id"]).startswith(group + ":")]
    if len(group_events) > batch["group_budgets"][group]:
        raise ValueError("Audit exceeds the group request budget")
    evidence = {"prepared_attempts": len(group_events), "known_sdk_attempts_in_results": sum(r["model_calls"] for r in aligned),
                "http_responses_recorded": sum(type(a.get("http_status")) is int for a in group_events),
                "indeterminate_attempts": sum(a["result_status"] == "INDETERMINATE" for a in group_events),
                "unattributed_prepared_attempt_ids": [a["attempt_id"] for a in group_events if a["attempt_id"] not in consumed],
                "audit_state": audit["audit_state"], "actual_transmissions": "UNKNOWN", "automatic_resume_allowed": False}
    evidence["counts_complete"] = (evidence["audit_state"] == "VALID" and evidence["indeterminate_attempts"] == 0
                                    and not evidence["unattributed_prepared_attempt_ids"])
    return {"config": config, "batch": batch, "rows": aligned, "metadata": metadata,
            "missing_case_ids": missing, "request_evidence": evidence,
            "source_result_sha256": file_sha256(result_path) if result_path.exists() else None}
