"""Opt-in real BIRD dev33 platform integration with explicitly scripted SDKs."""
import json
import os
from pathlib import Path

import pytest

from bird_starter.ablation import run_suite, PROJECT_ROOT
from bird_starter.data import dataset_paths
from bird_starter.experiment_sets import file_sha256


@pytest.mark.bird_integration
def test_real_ablation_four_groups_33(tmp_path):
    if os.environ.get("BIRD_RUN_INTEGRATION") != "1":
        pytest.skip("Real four-group ablation requires downloaded BIRD and explicit integration opt-in")
    cases, root = dataset_paths()
    config = PROJECT_ROOT / "configs/phase3c/ablation_v1.json"
    manifest = PROJECT_ROOT / "configs/phase3a/bird_dev33_v1.json"
    before = file_sha256(manifest)
    output = Path(os.environ["BIRD_ABLATION_REPORT_DIR"]) if os.environ.get("BIRD_ABLATION_REPORT_DIR") else tmp_path / "real_mock_suite"
    summary = run_suite(config, cases, root, output, execution="mock", mock_scenario="fault_injection_v1")
    assert summary["complete"] and summary["remote_api_requests"] == 0
    assert summary["batch_budget"] == 264 and summary["model_calls"] == 242
    assert summary["request_audit"]["prepared_attempts"] == 242
    assert summary["request_audit"]["indeterminate_attempts"] == 0
    assert summary["sqlite_sha256_unchanged"] and file_sha256(manifest) == before
    expected_ids = [r["case_id"] for r in json.loads(manifest.read_text())["cases"]]
    for group, calls, successes, repairs in (("A", 33, 11, 0), ("B", 66, 11, 0), ("C", 55, 33, 22), ("D", 88, 33, 22)):
        rows = [json.loads(line) for line in (output / f"{group}.jsonl").read_text(encoding="utf-8").splitlines()]
        metrics = summary["group_summaries"][group]
        assert len(rows) == metrics["planned_cases"] == 33 and len({r["db_id"] for r in rows}) == 11
        assert [r["case_id"] for r in rows] == expected_ids
        assert metrics["model_calls"] == calls and metrics["final_execution_successes"] == successes
        assert metrics["initial_execution_successes"] == 11 and metrics["repair_attempts"] == repairs
        assert metrics["usage_missing_calls"] == calls and metrics["prompt_tokens"] is None
        assert all(r["offline_mock"] and r["fault_injection"] for r in rows)
        assert all(r["model_calls"] <= {"A": 1, "B": 2, "C": 2, "D": 3}[group] for r in rows)
        assert metrics["local_result_match_proxy_rate"] == metrics["local_result_match_proxy_matches"] / 33
    assert len(summary["paired_comparisons"]) == 6
