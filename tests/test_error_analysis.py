"""Offline analyzer integration; all cases/scores here are test fixtures only."""
import json

import pytest

import bird_starter.experiment_review as review
import bird_starter.error_analysis as analysis
from bird_starter.ablation import run_suite
from bird_starter.error_analysis import build_error_report, classify_record, node_summary
from bird_starter.experiment_sets import file_sha256
from bird_starter.official_ex_inputs import OFFICIAL_COMMIT, PROTOCOL
from scripts.analyze_errors import main
from test_ablation import ScriptedSDK, records, suite_files


@pytest.fixture
def saved_run(tmp_path, suite_files, monkeypatch):
    monkeypatch.setattr(review, "PRIVATE_OUTPUT_ROOT", tmp_path)
    folder = tmp_path / "run"
    run_suite(*suite_files, folder, groups=("A", "B"), execution="mock", client_factory=lambda g: ScriptedSDK())
    return folder, suite_files[0]


def score_fixture(folder, group, suite, path):
    """Invented scorer envelope ONLY for provenance/format unit tests."""
    config = json.loads(suite.read_text())
    upstream = json.loads((review.PROJECT_ROOT / "configs/official_ex/upstream.json").read_text())
    payload = {"status": "COMPLETED", "protocol": PROTOCOL, "official_commit": OFFICIAL_COMMIT,
               "official_source_sha256": {k: v["sha256"] for k, v in upstream["files"].items()},
               "scoring_logic_modified": False, "source_result_sha256": file_sha256(folder / f"{group}.jsonl"),
               "manifest_sha256": config["manifest_sha256"], "measurement_kind": "OFFLINE_MOCK_NOT_MODEL_QUALITY",
               "planned_cases": 3, "correct": 1,
               "rows": [{"case_id": r["case_id"], "db_id": r["db_id"], "res": int(i == 0),
                         "status": "OK" if i == 0 else "MISMATCH"}
                        for i, r in enumerate(records(folder, group))]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_new_run_has_complete_schema_and_node_evidence(saved_run, tmp_path):
    folder, suite = saved_run
    before = {p.name: file_sha256(p) for p in folder.iterdir()}
    report = build_error_report({"A": folder, "B": folder}, suite, tmp_path / "analysis")
    assert report["model_requests"] == report["sql_executions"] == 0
    assert report["groups"]["A"]["schema_evidence_counts"] == {"EXACT": 3}
    assert report["groups"]["B"]["schema_evidence_counts"] == {"EXACT": 3}
    for g in "AB":
        assert report["groups"][g]["planned_cases"] == 3
        assert report["groups"][g]["official_correct"] is None
        assert report["groups"][g]["node_summary"]["generation"]["model_calls"] == 3
        assert report["groups"][g]["node_summary"]["generation"]["prompt_tokens"] is None
        assert "observability.py" in report["groups"][g]["source_sha256"]
        assert all(r["prompt_versions"]["prompt_version"] for r in records(folder, g))
    serialized = json.dumps(report)
    assert "GOLD_ONLY_SUITE_SENTINEL" not in serialized and "CREATE TABLE" not in serialized
    assert "SUM(amount)" not in serialized
    assert before == {p.name: file_sha256(p) for p in folder.iterdir()}
    with pytest.raises(FileExistsError):
        build_error_report({"A": folder}, suite, tmp_path / "analysis")


def test_historical_missing_fields_are_not_backfilled(saved_run, tmp_path):
    folder, suite = saved_run
    rows = records(folder, "B")
    for row in rows:
        row.pop("schema_observation")
        row.pop("prompt_versions")
    (folder / "B.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report = build_error_report({"B": folder}, suite, tmp_path / "legacy")
    assert report["groups"]["B"]["schema_evidence_counts"] == {"UNKNOWN_NOT_RECORDED": 3}
    assert all(c["prompt_versions"] is None for c in report["cases"])


def test_official_mismatch_and_manual_annotation_are_separate(saved_run, tmp_path):
    folder, suite = saved_run
    scores = tmp_path / "test_score_fixture.json"
    score_fixture(folder, "A", suite, scores)
    annotation = {"experiment_group": "A", "case_id": 2, "db_id": "demo",
        "source_result_sha256": file_sha256(folder / "A.jsonl"), "category": "filter_extra",
        "confidence": "LIKELY", "evidence": "Manual test fixture claim", "reviewer": "fixture-reviewer"}
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"annotations": [annotation]}))
    report = build_error_report({"A": folder}, suite, tmp_path / "official", official_summaries={"A": scores}, annotations=annotations)
    assert report["groups"]["A"]["outcome_counts"] == {"official_match": 1, "semantic_mismatch_unattributed": 2}
    assert report["groups"]["A"]["manual_attribution_count"] == 1
    assert report["cases"][1]["manual_attribution"]["confidence"] == "LIKELY"
    assert report["cases"][2]["manual_attribution"] is None
    assert report["groups"]["A"]["official_correct"] == 1


@pytest.mark.parametrize("damage", ["source", "case", "duplicate", "model_kind", "score", "commit", "protocol", "source_code", "count"])
def test_official_provenance_drift_rejected(saved_run, tmp_path, damage):
    folder, suite = saved_run
    path = tmp_path / "scores.json"
    payload = score_fixture(folder, "A", suite, path)
    if damage == "source": payload["source_result_sha256"] = "other-run"
    elif damage == "case": payload["rows"].reverse()
    elif damage == "duplicate": payload["rows"][1]["case_id"] = payload["rows"][0]["case_id"]
    elif damage == "model_kind": payload["measurement_kind"] = "REAL_MODEL"
    elif damage == "score": payload["rows"][0]["res"] = True
    elif damage == "commit": payload["official_commit"] = "different"
    elif damage == "protocol": payload["protocol"] = "different"
    elif damage == "source_code": payload["official_source_sha256"] = {}
    else: payload["correct"] = 99
    path.write_text(json.dumps(payload))
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        build_error_report({"A": folder}, suite, output, official_summaries={"A": path})
    assert not output.exists()


@pytest.mark.parametrize("damage", ["hash", "category", "duplicate", "matched", "reviewer"])
def test_manual_annotations_require_identity_and_eligible_mismatch(saved_run, tmp_path, damage):
    folder, suite = saved_run
    scores = tmp_path / "score.json"
    score_fixture(folder, "A", suite, scores)
    annotation = {"experiment_group": "A", "case_id": 2, "db_id": "demo",
        "source_result_sha256": file_sha256(folder / "A.jsonl"), "category": "join_error",
        "confidence": "LIKELY", "evidence": "Synthetic review fixture", "reviewer": "fixture"}
    if damage == "hash": annotation["source_result_sha256"] = "other"
    elif damage == "category": annotation["category"] = "automatic_gold_repair"
    elif damage == "matched": annotation["case_id"] = 1
    elif damage == "reviewer": annotation["reviewer"] = ""
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"annotations": [annotation] * (2 if damage == "duplicate" else 1)}))
    with pytest.raises(ValueError):
        build_error_report({"A": folder}, suite, tmp_path / "bad", official_summaries={"A": scores}, annotations=annotations)


@pytest.mark.parametrize("updates,score,expected", [
    ({"processed": False}, None, "not_run_or_missing"),
    ({"measurement_kind": "DRY_RUN"}, None, "dry_run"),
    ({"generation_success": False, "error_category": "model_output_truncated"}, None, "generation_truncated"),
    ({"generation_success": False, "error_category": "model_network_error"}, None, "generation_failure"),
    ({"output_truncated": True}, {"res": 0, "status": "UNSAFE_SQL"}, "result_truncated"),
    ({"final_execution_ok": False, "error_category": "sql_timeout"}, None, "execution_timeout"),
    ({"final_execution_ok": False, "error_category": "syntax"}, None, "syntax_error"),
    ({"final_execution_ok": False, "error_category": "missing_table"}, None, "schema_execution_error"),
    ({"final_execution_ok": False, "error_category": "safety_rejected"}, None, "safety_rejected"),
    ({}, {"res": 0, "status": "SQL_TIMEOUT"}, "official_evaluation_failure_or_unknown"),
    ({}, {"res": 0, "status": "MISMATCH"}, "semantic_mismatch_unattributed"),
    ({"local_result_match_proxy": False}, None, "proxy_unmatched_or_evaluator_error"),
    ({"local_result_match_proxy": True}, None, "proxy_match_only"),
    ({}, None, "unassessable"),
])
def test_observed_outcomes_do_not_infer_semantic_causes(updates, score, expected):
    row = {"processed": True, "generation_success": True, "final_execution_ok": True}
    row.update(updates)
    assert classify_record(row, score) == expected


def test_partial_node_usage_remains_unknown():
    report = node_summary([{"node_metrics": {"generation": {"model_calls": 1, "prompt_tokens": 5}}}, {}])
    assert report["generation"]["prompt_tokens"] is None
    assert report["generation"]["known_prompt_tokens_subtotal"] == 5
    assert report["generation"]["model_calls"] is None


def test_cli_and_private_output_restrictions(saved_run, tmp_path, capsys):
    folder, suite = saved_run
    args = ["--run", f"A={folder}", "--suite-config", str(suite), "--out-dir", str(tmp_path / "cli")]
    assert main(args) == 0
    assert "model requests 0" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(args + ["--run", f"A={folder}"])
    with pytest.raises(ValueError):
        build_error_report({"A": folder}, suite, tmp_path.parent / "outside")


def test_missing_cases_kept_in_fixed_denominator(saved_run, tmp_path):
    folder, suite = saved_run
    # A missing result leaves an unattributed audit event, not a free request.
    rows = records(folder, "A")[:2]
    (folder / "A.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = build_error_report({"A": folder}, suite, tmp_path / "missing")
    assert report["groups"]["A"]["planned_cases"] == 3
    assert report["groups"]["A"]["outcome_counts"]["not_run_or_missing"] == 1
    assert not report["groups"]["A"]["request_evidence"]["counts_complete"]


def test_input_mutation_during_analysis_rejected(saved_run, tmp_path, monkeypatch):
    folder, suite = saved_run
    read = analysis.read_group
    def mutate_after_read(*args):
        context = read(*args)
        with (folder / "A.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        return context
    monkeypatch.setattr(analysis, "read_group", mutate_after_read)
    with pytest.raises(ValueError, match="changed during reading"):
        build_error_report({"A": folder}, suite, tmp_path / "mutated")
    assert not (tmp_path / "mutated").exists()


def test_prompt_version_drift_rejected(saved_run, tmp_path):
    folder, suite = saved_run
    rows = records(folder, "B")
    rows[0]["prompt_versions"]["prompt_version"] = "unrecorded-template"
    (folder / "B.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(ValueError, match="Prompt version"):
        build_error_report({"B": folder}, suite, tmp_path / "wrong_version")
