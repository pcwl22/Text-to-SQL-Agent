"""P1B metadata-only sampling and immutable, generation-side batch preparation."""
import json
import math
import os
from pathlib import Path
import random
import subprocess

from .data import find_db
from .experiment_sets import file_sha256
from .prompts import PROMPT_VERSION
from .request_audit import write_new_json
from .schema_cost_analysis import project_inputs
from .schema_linking import LINK_PROMPT_VERSION, read_schema
from .schema_routing import AdaptiveSchemaGate, route_schema

ROOT = Path(__file__).resolve().parents[1]
VERSION = "p1b-adaptive-live-v1"
SAMPLER = "sorted-integer-id-random-sample-skip5-use5-shuffle-v1"
SEED = 20261023


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def validate_config(config):
    allowed = {"version", "dataset_version", "dataset_sha256", "dataset_question_count", "case_count", "database_sha256",
               "development_manifest", "development_manifest_sha256", "seed", "threshold_bytes", "groups", "max_attempts",
               "common", "pricing", "api_authorization", "user_cost_preference", "manifest_sha256", "source_sha256",
               "git_head", "source_config_sha256"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError("Unexpected batch configuration fields")
    c = config["common"]
    expected_common = {"model", "provider", "base_url", "temperature", "request_timeout_seconds", "request_retries",
                       "sdk_retries", "n", "max_output_tokens", "token_parameter", "concurrency", "evidence", "prompt_version",
                       "sql_timeout_seconds", "max_rows", "max_prompt_chars", "http_redirects", "max_repairs", "schema_link_prompt_version"}
    if set(c) != expected_common:
        raise ValueError("Unexpected model configuration fields")
    for key in ("n", "max_output_tokens", "concurrency", "request_retries", "sdk_retries", "max_repairs"):
        if type(c[key]) is not int:
            raise ValueError("Integer protocol fields cannot be booleans")
    for group in config["groups"].values():
        if type(group.get("adaptive")) is not bool or type(group.get("max_attempts")) is not int:
            raise ValueError("Strict group switch and budget types required")
    pricing_fields = {"status", "model", "provider", "base_url", "billing_mode", "input_per_million", "output_per_million",
                      "cached_input_per_million", "currency", "source_url", "verified_at", "additional_charges", "usage_supported",
                      "byte_token_bound_verified", "chat_overhead_token_bound", "bound_source_url", "notice", "account_plan_evidence",
                      "included_model_verified", "no_incremental_inference_charge_verified"}
    if set(config["pricing"]) - pricing_fields:
        raise ValueError("Unexpected pricing fields; never place credentials in config")
    if (config["version"] != VERSION or config["seed"] != SEED
            or config["case_count"] != 10 or config["threshold_bytes"] != 1014
            or config["groups"] != {"B": {"adaptive": False, "max_attempts": 20},
                                    "E": {"adaptive": True, "max_attempts": 20}}
            or type(config["max_attempts"]) is not int or not 1 <= config["max_attempts"] <= 40
            or c["temperature"] != 0 or c["n"] != 1 or c["max_output_tokens"] != 2048
            or c["concurrency"] != 1 or c["request_retries"] != 0 or c["sdk_retries"] != 0
            or c["http_redirects"] is not False or c["max_repairs"] != 0
            or c["prompt_version"] != PROMPT_VERSION or c["schema_link_prompt_version"] != LINK_PROMPT_VERSION
            or type(c["evidence"]) is not bool):
        raise ValueError("P1B protocol/configuration mismatch")
    for name in ("model", "provider", "base_url"):
        if not isinstance(c[name], str) or not c[name].strip():
            raise ValueError("An explicit provider/model/endpoint is required")
    from .model import safe_base_url
    if not safe_base_url(c["base_url"]).startswith("https://"):
        raise ValueError("Live candidate endpoint must use HTTPS")
    for name in ("request_timeout_seconds", "sql_timeout_seconds"):
        if type(c[name]) not in (int, float) or not math.isfinite(c[name]) or c[name] <= 0:
            raise ValueError("Invalid timeout")
    for name, maximum in (("max_rows", 10000), ("max_prompt_chars", 200000)):
        if type(c[name]) is not int or not 0 < c[name] <= maximum:
            raise ValueError("Invalid execution/prompt bound")
    if c["token_parameter"] not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("Invalid output-token parameter")
    return config


def source_hashes():
    paths = sorted((ROOT / "bird_starter").glob("*.py"))
    paths += [ROOT / "scripts" / name for name in (
        "run_adaptive_live_validation.py", "compare_adaptive_validation.py", "export_official_ex_inputs.py", "run_official_ex.py")]
    return {p.relative_to(ROOT).as_posix(): file_sha256(p) for p in paths}


def git_head():
    # Offline tests remove secret-named variables, including GIT_CONFIG_KEY_n.
    # Never inherit a dangling GIT_CONFIG_COUNT or credentials into this local check.
    names = ("SystemRoot", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP")
    env = {key: os.environ[key] for key in names if key in os.environ}
    try:
        return subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                       env=env, stderr=subprocess.DEVNULL, timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Cannot verify local Git HEAD") from None


def verified_inputs(config, cases_path, db_root):
    if file_sha256(cases_path) != config["dataset_sha256"]:
        raise ValueError("Dataset fingerprint mismatch")
    projected = project_inputs(cases_path)  # JSON decoding discards Gold and score fields.
    if (len(projected) != config["dataset_question_count"]
            or any(type(key) is not int for key in projected)):
        raise ValueError("Classic integer identities/count required")
    dev_path = ROOT / config["development_manifest"]
    if file_sha256(dev_path) != config["development_manifest_sha256"]:
        raise ValueError("Development manifest fingerprint mismatch")
    dev = read_json(dev_path)
    if dev["dataset_sha256"] != config["dataset_sha256"]:
        raise ValueError("Development dataset mismatch")
    excluded = {r["case_id"] for r in dev["cases"]}
    if len(excluded) != len(dev["cases"]) or not excluded <= projected.keys():
        raise ValueError("Invalid development exclusion identities")
    paths, distribution = {}, {}
    for db, expected in config["database_sha256"].items():
        path = find_db(db_root, db)
        if file_sha256(path) != expected:
            raise ValueError("SQLite fingerprint mismatch")
        paths[db] = path
        distribution[db] = route_schema(read_schema(path), AdaptiveSchemaGate(True, 1014))
    if {r["db_id"] for r in projected.values()} != paths.keys():
        raise ValueError("Database catalog mismatch")
    return projected, excluded, paths, distribution


def sample_metadata(projected, excluded, distribution):
    """Identity and metadata only; never inspect a question, SQL or score."""
    rng = random.Random(SEED)
    chosen, available = [], {}
    for decision in ("SKIP_LINKING", "USE_LINKING"):
        pool = sorted(key for key, row in projected.items()
                      if key not in excluded and distribution[row["db_id"]]["routing_decision"] == decision)
        available[decision] = len(pool)
        if len(pool) < 5:
            raise ValueError("Insufficient routing strata: " + json.dumps(available))
        chosen.extend(rng.sample(pool, 5))
    rng.shuffle(chosen)
    cases = [{"case_id": key, "db_id": projected[key]["db_id"],
              "difficulty": projected[key].get("difficulty", "unknown")} for key in chosen]
    # Paired adjacent strategies, alternating which runs first: five B/E, five E/B.
    schedule = [{"group": group, "case_id": row["case_id"]}
                for index, row in enumerate(cases) for group in (("B", "E") if index % 2 == 0 else ("E", "B"))]
    return cases, schedule, available


def prepare(config_path, cases_path, db_root, out_dir):
    from .experiment_review import reserve_private_output
    config = validate_config(read_json(config_path))
    projected, excluded, paths, distribution = verified_inputs(config, cases_path, db_root)
    cases, schedule, available = sample_metadata(projected, excluded, distribution)
    manifest = {"manifest_version": VERSION, "source_revision": config["dataset_version"],
                "purpose": "new_question_holdout_on_same_databases_not_cross_database_generalization",
                "dataset_sha256": config["dataset_sha256"], "cases": cases,
                "case_count": 10, "seed": SEED, "sampling_algorithm": SAMPLER,
                "selection_uses_gold_sql": False, "excluded_manifest_sha256": config["development_manifest_sha256"],
                "threshold_bytes": 1014, "schedule": schedule, "available_strata": available,
                "database_sha256": config["database_sha256"], "schema_distribution": distribution,
                "model_and_parameters": config["common"]}
    output = reserve_private_output(out_dir)
    write_new_json(output / "manifest.json", manifest)
    config.update(manifest_sha256=file_sha256(output / "manifest.json"),
                  source_sha256=source_hashes(), git_head=git_head(),
                  source_config_sha256=file_sha256(config_path))
    write_new_json(output / "config.json", config)
    write_new_json(output / "freeze.json", {"config_sha256": file_sha256(output / "config.json"),
                                          "manifest_sha256": config["manifest_sha256"]})
    return manifest


def load_frozen(folder, cases_path, db_root):
    folder = Path(folder)
    seal = read_json(folder / "freeze.json")
    config = validate_config(read_json(folder / "config.json"))
    manifest = read_json(folder / "manifest.json")
    if (seal != {"config_sha256": file_sha256(folder / "config.json"),
                 "manifest_sha256": file_sha256(folder / "manifest.json")}
            or config["manifest_sha256"] != seal["manifest_sha256"]
            or config["git_head"] != git_head() or config["source_sha256"] != source_hashes()):
        raise ValueError("Frozen source/configuration/HEAD changed; never resume")
    projected, excluded, paths, distribution = verified_inputs(config, cases_path, db_root)
    cases, schedule, available = sample_metadata(projected, excluded, distribution)
    if (manifest["cases"] != cases or manifest["schedule"] != schedule
            or manifest["schema_distribution"] != distribution
            or manifest["available_strata"] != available
            or manifest["sampling_algorithm"] != SAMPLER or manifest["seed"] != SEED
            or manifest["threshold_bytes"] != 1014 or manifest["model_and_parameters"] != config["common"]
            or manifest["database_sha256"] != config["database_sha256"]
            or manifest["dataset_sha256"] != config["dataset_sha256"]):
        raise ValueError("Frozen sample/order/route metadata mismatch")
    return config, manifest, projected, paths
