"""One authorized catalog GET, not a generation probe. Never expose credentials."""
from datetime import datetime, timezone
import os
from pathlib import Path

import httpx

from . import config as environment
from . import adaptive_validation_budget as budgets
from .adaptive_validation_config import load_frozen, read_json
from .experiment_review import reserve_private_output
from .experiment_sets import file_sha256
from .model import safe_base_url
from .request_audit import RequestLog, inspect_request_log, write_new_json


def credentials_ready(common, *, project_env=False):
    """Use the existing secure loader only on explicitly authorized connection/Live paths.

    This returns booleans and public configuration, never a secret or env dump.
    """
    previous = os.environ.get("PYTHON_DOTENV_DISABLED")
    if project_env:
        os.environ.pop("PYTHON_DOTENV_DISABLED", None)
    else:
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    try:
        environment.load_project_env()
    finally:
        if previous is None:
            os.environ.pop("PYTHON_DOTENV_DISABLED", None)
        else:
            os.environ["PYTHON_DOTENV_DISABLED"] = previous
    if not isinstance(os.environ.get("OPENAI_API_KEY"), str) or not os.environ["OPENAI_API_KEY"].strip():
        raise ValueError("Credential unavailable; do not switch provider")
    if os.environ.get("OPENAI_MODEL") and os.environ["OPENAI_MODEL"] != common["model"]:
        raise ValueError("Configured model differs from frozen model")
    if os.environ.get("OPENAI_BASE_URL") and safe_base_url(os.environ["OPENAI_BASE_URL"]) != common["base_url"]:
        raise ValueError("Configured endpoint differs from frozen endpoint")
    return {"model": common["model"], "base_url": common["base_url"], "credential_present": True,
            "configuration_matches": True, "secrets_recorded": False}


def receipt_paths(auth):
    folder = budgets.ROOT / "outputs" / "p1b_authorization_consumption"
    return folder, folder / (auth["authorization_id"] + ".connection.json"), folder / (auth["authorization_id"] + ".connection.complete.json")


def verify_connection_receipt(auth, config):
    folder, consumed, completed = receipt_paths(auth)
    value = read_json(completed)
    report_path = Path(value["report_path"])
    report = read_json(report_path)
    if (not consumed.is_file() or value["report_sha256"] != file_sha256(report_path)
            or report["status"] != "PASS" or report["authorization_id"] != auth["authorization_id"]
            or report["config_sha256"] != auth["config_sha256"] or report["model"] != config["common"]["model"]
            or report["base_url"] != config["common"]["base_url"] or report["catalog_reservations"] != 1):
        raise ValueError("Fresh authorized connection receipt missing/mismatched")
    return report


def check_connection(frozen_dir, cases_path, db_root, out_dir, authorization, *, project_env=False, client=None):
    config, manifest, projected, paths = load_frozen(frozen_dir, cases_path, db_root)
    auth = budgets.verify_live_authorization(authorization, config, frozen_dir)
    if auth.get("cost_policy") != budgets.RECORD_ONLY:
        raise ValueError("Catalog check requires this batch's explicit combined request authorization")
    public = credentials_ready(config["common"], project_env=project_env)
    output = reserve_private_output(out_dir)
    folder, consumed, completed = receipt_paths(auth)
    folder.mkdir(parents=True, exist_ok=True)
    write_new_json(consumed, {"authorization_id": auth["authorization_id"], "catalog_allowance": 1,
                              "automatic_retry_allowed": False, "output": str(output)})
    log = RequestLog(output / "catalog.attempts.jsonl", auth["authorization_id"], public["model"], 1)
    status, http_status, ident = "FAIL", None, None
    owned = client is None
    connection = client or httpx.Client(timeout=30, follow_redirects=False, transport=None)
    try:
        ident = log.prepare("CONTROL:catalog:availability")
        response = connection.get(public["base_url"] + "/models", headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
        http_status = response.status_code
        if http_status != 200:
            raise ValueError("Model catalog endpoint rejected; no redirect or retry")
        data = response.json()
        ids = [row.get("id") for row in data["data"] if isinstance(row, dict)]
        if public["model"] not in ids:
            raise ValueError("Frozen model not present in authenticated catalog")
        log.finish(ident, "SUCCEEDED", http_status=http_status)
        status = "PASS"
    except BaseException:
        if ident in log.pending:
            log.finish(ident, "INDETERMINATE" if http_status is None else "FAILED", http_status=http_status,
                       error_category="catalog_unavailable_or_model_unverified")
    finally:
        log.close()
        if owned:
            connection.close()
    audit = inspect_request_log(log.path, auth["authorization_id"], 1)
    report = {**public, "status": status, "authorization_id": auth["authorization_id"],
              "checked_at_utc": datetime.now(timezone.utc).isoformat(), "config_sha256": auth["config_sha256"],
              "catalog_reservations": audit["prepared_attempts"], "http_status": http_status,
              "model_generation_requests": 0, "audit": audit}
    write_new_json(output / "connection.json", report)
    if status != "PASS":
        raise ValueError("Connection/model verification failed; catalog allowance consumed; no retry")
    write_new_json(completed, {"report_path": str((output / "connection.json").resolve()),
                               "report_sha256": file_sha256(output / "connection.json")})
    return report
