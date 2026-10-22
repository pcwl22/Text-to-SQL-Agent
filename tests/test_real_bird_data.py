"""Explicit real-data integration check; never substitutes synthetic fixtures."""
import os
import json
from contextlib import closing
from pathlib import Path

import pytest

from scripts.validate_dataset import file_fingerprints, validate_dataset
from bird_starter.data import AgentInput, dataset_paths, find_db, load_cases
from bird_starter.tools import execute_select, ro_connect


@pytest.mark.bird_integration
def test_real_bird_dataset():
    if os.environ.get("BIRD_RUN_INTEGRATION") != "1":
        pytest.skip("Real BIRD integration UNVERIFIED; set BIRD_RUN_INTEGRATION=1 after downloading data")
    cases, db_root = dataset_paths()
    records = load_cases(cases)
    paths = {case["db_id"]: None for case in records}
    paths = {db_id: find_db(db_root, db_id) for db_id in paths}
    before = {db_id: file_fingerprints(path)["sha256"] for db_id, path in paths.items()}
    report = validate_dataset(cases, db_root, check_compatibility=True)
    report_path = os.environ.get("BIRD_INTEGRATION_REPORT")
    def save_report():
        if report_path:
            output = Path(report_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save_report()


    assert report["ok"], report["errors"]
    assert report["matched_question_count"] == 500
    assert report["schema_compatibility"]["failed"] == 0
    for entry in report["databases"]:
        db_id, path = entry["db_id"], paths[entry["db_id"]]
        with closing(ro_connect(path)) as conn:
            assert conn.execute("PRAGMA query_only").fetchone() == (1,)
        # Construct from schema, not from any case's SQL. Do not log business rows.
        table = entry["schema_preview"][0]["table"].replace('"', '""')
        query = f'SELECT 1 FROM "{table}" LIMIT 1'
        result = execute_select(path, query, max_rows=1)
        entry["read_only_sample"] = {"query": query, "row_count": len(result["rows"]), "ok": True}
        entry["query_only"] = True
        entry["sha256_before"] = before[db_id]
        entry["sha256_after"] = file_fingerprints(path)["sha256"]
        entry["bytes_unchanged"] = entry["sha256_before"] == entry["sha256_after"]
        assert entry["bytes_unchanged"], db_id
    for case in records:
        state = AgentInput.from_case(case, paths[case["db_id"]], "baseline").to_state()
        assert set(state) == {"question", "evidence", "db_path", "mode"}
    report["agent_inputs_checked"] = len(records)
    save_report()


@pytest.mark.bird_integration
def test_real_baseline_preflight_without_model():
    if os.environ.get("BIRD_RUN_INTEGRATION") != "1":
        pytest.skip("Real BIRD baseline preflight is opt-in")
    from bird_starter.baseline import run_baseline
    cases, db_root = dataset_paths()
    records = load_cases(cases, limit=10)
    assert len(records) == 10
    for case in records:
        agent_input = AgentInput.from_case(case, find_db(db_root, case["db_id"]), "baseline")
        output = run_baseline(agent_input, None, dry_run=True)
        assert output["preflight_ok"] is True
        assert output["generated_sql"] == "" and output["generation_success"] is None
