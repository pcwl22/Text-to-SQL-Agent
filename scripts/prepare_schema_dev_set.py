"""Prepare classic real-data manifests offline. Never constructs a model or reads credentials."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.data import load_cases
from bird_starter.experiment_sets import make_dev_manifest, file_sha256
from bird_starter.request_audit import write_new_json
from scripts.validate_dataset import EXPECTED_GIT_BLOB, SOURCE_REVISION, file_fingerprints


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--baseline-config", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    source = Path(args.cases)
    fingerprint = file_fingerprints(source)
    if fingerprint["git_blob_sha1"] != EXPECTED_GIT_BLOB:
        parser.error("Expected pinned classic Mini-Dev question bytes; no replacement data generated")
    records = load_cases(source)
    manifest = make_dev_manifest(records, fingerprint["sha256"], SOURCE_REVISION)
    if (len(records), manifest["case_count"], manifest["database_count"]) != (500, 33, 11):
        parser.error("Expected 500 source cases, 33 selected cases and 11 databases")
    baseline_path = Path(args.baseline_config)
    baseline = json.loads(baseline_path.read_text())
    if baseline["dataset_sha256"] != fingerprint["sha256"]:
        parser.error("Baseline/data fingerprint mismatch")
    keys = ("model", "base_url", "temperature", "request_timeout_seconds", "request_retries", "sdk_retries",
            "n", "max_output_tokens", "token_parameter", "concurrency", "evidence", "prompt_version",
            "sql_timeout_seconds", "max_rows", "max_prompt_chars", "http_redirects")
    common = {key: baseline[key] for key in keys}
    suite = {"suite_version": "ablation-common-v1", "dataset_sha256": fingerprint["sha256"],
             "source_revision": SOURCE_REVISION, "common_configuration": common,
             "baseline_reference_run_id": baseline["run_id"], "baseline_config_sha256": file_sha256(baseline_path),
             "smoke_case_ids": baseline["case_ids"], "development_manifest": "bird_dev33_v1.json",
             "api_authorization": "NONE; new authorization required for every future experiment",
             "request_budget_policy": "Explicit per-run cap required; linking adds at most one request per case; never borrow V3 budget",
             "groups": {"baseline": {"schema_linking": False, "repair": False, "implemented": True},
                        "schema_only": {"schema_linking": True, "repair": False, "implemented": True},
                        "repair_only": {"schema_linking": False, "repair": True, "implemented": False},
                        "full": {"schema_linking": True, "repair": True, "implemented": False}}}
    folder = Path(args.out_dir)
    targets = [folder / "bird_dev33_v1.json", folder / "ablation_common_v1.json"]
    if any(p.exists() for p in targets) or folder.resolve().is_relative_to(source.resolve().parent):
        parser.error("Output must be fresh and outside dataset directory")
    folder.mkdir(parents=True, exist_ok=True)
    write_new_json(targets[0], manifest)
    write_new_json(targets[1], suite)
    print(json.dumps({"cases": 33, "databases": 11, "difficulty_counts": manifest["difficulty_counts"],
                      "files": [str(p) for p in targets], "model_api_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
