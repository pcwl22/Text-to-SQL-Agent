"""Real SQLite + Mock graph. This measures schema mechanics, never model/SQL accuracy."""
from collections import Counter
import json
import os
from pathlib import Path

import pytest

from bird_starter.data import load_cases, find_db, dataset_paths, AgentInput
from bird_starter.experiment_sets import file_sha256, select_manifest_cases
from bird_starter.schema_linking import read_schema
from bird_starter.schema_workflow import build_schema_graph, schema_graph_record
from bird_starter.schema_evaluation import evaluate_selection, summarize_schema
from bird_starter.request_audit import write_new_json


@pytest.mark.bird_integration
def test_real_dev33_mock_schema_graph():
    if os.environ.get("BIRD_RUN_INTEGRATION") != "1":
        pytest.skip("Real schema-linking integration requires opt-in downloaded BIRD data")
    project = Path(__file__).resolve().parents[1]
    manifest_path = project / "configs/phase3a/bird_dev33_v1.json"
    cases_path, db_root = dataset_paths()
    cases = select_manifest_cases(load_cases(cases_path), manifest_path, file_sha256(cases_path))
    assert len(cases) == 33 and len({c["db_id"] for c in cases}) == 11
    assert Counter(c["difficulty"] for c in cases) == {"simple": 11, "moderate": 11, "challenging": 11}
    paths = {c["db_id"]: find_db(db_root, c["db_id"]) for c in cases}
    before = {name: file_sha256(path) for name, path in paths.items()}
    snapshots = {name: read_schema(path) for name, path in paths.items()}
    records = []
    for index, case in enumerate(cases):
        snapshot = snapshots[case["db_id"]]
        first_table = next(iter(snapshot["tables"]))
        first_column = snapshot["tables"][first_table]["columns"][0]["name"]
        # Deliberately naive, metadata-only Mock selector: no Gold or question-derived answers.
        answer = "invalid JSON fixture" if index % 11 == 0 else json.dumps({
            "tables": [{"name": first_table, "columns": [first_column]}]})
        class Mock:
            def __init__(self):
                self.calls = 0
            def ask(self, system, user):
                self.calls += 1
                return answer if self.calls == 1 else "SELECT 1"
        model = Mock()
        graph = build_schema_graph(model)
        output = graph.invoke(AgentInput.from_case(case, paths[case["db_id"]], "schema_only").to_state())
        assert output["execution_ok"] and model.calls == 2
        assert output["full_schema"] == snapshot["full_schema"]
        row = {"case_id": case["case_id"], "db_id": case["db_id"], **schema_graph_record(output),
               "offline_mock": True, "measurement_kind": "schema_mechanics_not_SQL_accuracy"}
        # Gold is read only now, after generation/execution has finished, by the offline evaluator.
        row["schema_evaluation"] = evaluate_selection(case["SQL"], snapshot, row["schema_selection"])
        records.append(row)
    for name, path in paths.items():
        assert file_sha256(path) == before[name]
    summary = summarize_schema(records)
    assert summary["selector_evaluated_cases"] == 33 and summary["selector_valid_cases"] == 30
    assert summary["fallback_cases"] == 3 and summary["effective_selection_legal_rate"] == 1
    assert summary["schema_linking_model_calls"] == 33 and summary["usage_missing_calls"] == 33
    assert summary["prompt_tokens"] is None and summary["completion_tokens"] is None
    folder = os.environ.get("BIRD_SCHEMA_REPORT_DIR")
    if folder:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        # Only validated selections/summaries leave the graph. No Gold SQL in records.
        with (folder / "mock33.jsonl").open("x", encoding="utf-8") as stream:
            for row in records:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        write_new_json(folder / "schema_metrics.json", {
            "measurement_kind": "OFFLINE_MOCK_NOT_REAL_MODEL_QUALITY", "remote_api_requests": 0,
            "manifest_sha256": file_sha256(manifest_path), "summary": summary,
            "cases": 33, "databases": 11, "mock_linking_calls": 33, "mock_generator_calls": 33,
            "sqlite_sha256_unchanged": True})


@pytest.mark.bird_integration
def test_real_manifests_reproduce_without_overwrite(tmp_path):
    if os.environ.get("BIRD_RUN_INTEGRATION") != "1":
        pytest.skip("Real manifest regeneration requires downloaded BIRD data")
    from scripts.prepare_schema_dev_set import main
    project = Path(__file__).resolve().parents[1]
    cases_path, _ = dataset_paths()
    out = tmp_path / "manifest-only"
    argv = ["--cases", str(cases_path), "--baseline-config",
            str(project / "outputs/phase2_live_v3/baseline_10_v3.config.json"), "--out-dir", str(out)]
    assert main(argv) == 0
    for name in ("bird_dev33_v1.json", "ablation_common_v1.json"):
        assert (out / name).read_bytes() == (project / "configs/phase3a" / name).read_bytes()
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert all((out / name).read_bytes() == value for name, value in before.items())
