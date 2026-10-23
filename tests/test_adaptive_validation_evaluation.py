"""Synthetic finalized artifact fixtures only; no real-model scores are made."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from test_adaptive_validation import prepared, dump, run
from bird_starter.adaptive_validation_config import read_json
from bird_starter.adaptive_validation_evaluation import compare_official, validated_results
from bird_starter.experiment_sets import file_sha256
from bird_starter.official_ex_inputs import (OFFICIAL_COMMIT, PROTOCOL, export_inputs, validate_bundle, FAILURE_SQL)
from bird_starter.official_ex_windows import SOURCE_SHA256


def synthetic_finalized_live_format(p):
    """Exercise the artifact reader with tiny test-only Live-format records.

    These synthetic fixtures are not a benchmark run or measurement evidence.
    """
    report = run(p, execution="mock")
    folder = p.tmp / "run"
    batch = read_json(folder / "batch.config.json")
    batch["execution"] = "live"
    dump(folder / "batch.config.json", batch)
    for g in "BE":
        rows = [json.loads(line) for line in (folder / f"{g}.jsonl").read_text().splitlines()]
        for row in rows:
            row["measurement_kind"] = "REAL_MODEL"
        (folder / f"{g}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report["execution"] = "live"
    report["artifacts_sha256"] = {name: file_sha256(folder / name) for name in report["artifacts_sha256"]}
    dump(folder / "preflight.json", report)
    return folder


def reseal(folder):
    report = read_json(folder / "preflight.json")
    report["artifacts_sha256"] = {name: file_sha256(folder / name) for name in report["artifacts_sha256"]}
    dump(folder / "preflight.json", report)


def test_normal_mock_is_not_eligible_for_new_official_ex(prepared):
    run(prepared, execution="mock")
    with pytest.raises(ValueError, match="reconciled real results"):
        export_inputs(prepared.tmp / "run", "E", prepared.frozen / "config.json", prepared.data, prepared.tmp / "official")
    assert not (prepared.tmp / "official").exists()


def test_new_adapter_fixed_ten_and_failed_truncated_slots_are_zero(prepared):
    folder = synthetic_finalized_live_format(prepared)
    rows = [json.loads(s) for s in (folder / "E.jsonl").read_text().splitlines()]
    rows[0].update(processed=False, generation_success=False)
    rows[1]["output_truncated"] = True
    (folder / "E.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    reseal(folder)
    out = prepared.tmp / "official"
    bundle = export_inputs(folder, "E", prepared.frozen / "config.json", prepared.data, out)
    assert bundle["planned_cases"] == validate_bundle(out)["aligned_cases"] == 10
    predictions = read_json(out / "predictions.json")
    assert predictions["0"].startswith(FAILURE_SQL) and predictions["1"].startswith(FAILURE_SQL)
    assert bundle["official_ex"] is None
    assert "GOLD_P1B_FIXTURE_SENTINEL" not in (out / "predictions.json").read_text()


@pytest.mark.parametrize("damage", ["order", "duplicate_attempt", "foreign_attempt", "eleven_rows", "mock_row", "unhashed_edit"])
def test_evaluator_rejects_identity_and_audit_drift(prepared, damage):
    folder = synthetic_finalized_live_format(prepared)
    path = folder / "B.jsonl"
    rows = [json.loads(s) for s in path.read_text().splitlines()]
    if damage == "order": rows[0], rows[1] = rows[1], rows[0]
    elif damage == "duplicate_attempt": rows[1]["request_attempt_ids"] = rows[0]["request_attempt_ids"]
    elif damage == "foreign_attempt": rows[0]["request_attempt_ids"][0] = "absent-fixture-attempt"
    elif damage == "eleven_rows": rows.append(rows[0])
    elif damage == "mock_row": rows[0]["measurement_kind"] = "OFFLINE_MOCK_NOT_MODEL_QUALITY"
    else: rows[0]["generated_sql"] = "SELECT 999"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    if damage != "unhashed_edit": reseal(folder)
    with pytest.raises(ValueError):
        validated_results(folder, "B", prepared.frozen / "config.json")


def official_fixture(p, folder, g):
    """Synthetic score metadata solely to test strict comparison provenance."""
    config = read_json(p.frozen / "config.json")
    return {"measurement_kind": "REAL_MODEL", "planned_cases": 10,
        "source_result_sha256": file_sha256(folder / f"{g}.jsonl"), "manifest_sha256": config["manifest_sha256"],
        "official_commit": OFFICIAL_COMMIT, "protocol": PROTOCOL, "official_source_sha256": SOURCE_SHA256,
        "scoring_logic_modified": False, "correct": 0, "official_ex": 0,
        "rows": [{**meta, "sql_idx": i, "res": 0} for i, meta in enumerate(p.manifest["cases"])]}


def test_paired_official_summary_requires_matching_provenance(prepared):
    folder = synthetic_finalized_live_format(prepared)
    b = dump(prepared.tmp / "b-summary.json", official_fixture(prepared, folder, "B"))
    e = dump(prepared.tmp / "e-summary.json", official_fixture(prepared, folder, "E"))
    result = compare_official(folder, prepared.frozen / "config.json", b, e, prepared.tmp / "comparison")
    assert result["planned_cases"] == len(result["paired_rows"]) == 10
    assert result["comparison"]["official_ex"]["difference_E_minus_B"] == 0
    assert result["groups"]["E"]["official_ex_status"] == "INDEPENDENT_OFFICIAL_EVALUATION"
    assert result["groups"]["E"]["official_denominator"] == 10
    assert result["comparison"]["total_tokens"]["B"] is None
    assert result["comparison"]["linking_request_reservations"] == {"B": 10, "E": 5, "difference_E_minus_B": -5}
    assert result["comparison"]["billed_cost_usd"]["B"] == "UNKNOWN"
    assert (prepared.tmp / "comparison/comparison.md").is_file()
    broken = read_json(e); broken["manifest_sha256"] = "wrong"; dump(e, broken)
    with pytest.raises(ValueError, match="provenance"):
        compare_official(folder, prepared.frozen / "config.json", b, e, prepared.tmp / "bad-comparison")


def test_pinned_official_engine_accepts_new_ten_slot_fixture_manifest(prepared):
    """Actual official sandbox on synthetic SELECT-1 controls, never model EX."""
    from bird_starter import official_ex_windows as win
    pytest.importorskip("func_timeout")
    pytest.importorskip("pymysql")
    pytest.importorskip("psycopg2")
    if not win.DEFAULT_SOURCE.is_dir():
        pytest.skip("Optional pinned official source cache unavailable; no download")
    folder = synthetic_finalized_live_format(prepared)
    rows = [json.loads(s) for s in (folder / "E.jsonl").read_text().splitlines()]
    rows[0]["output_truncated"] = True
    rows[1].update(processed=False, generation_success=False)
    (folder / "E.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    reseal(folder)
    bundle = prepared.tmp / "official_fixture_bundle"
    export_inputs(folder, "E", prepared.frozen / "config.json", prepared.data, bundle)
    output = prepared.tmp / "official_fixture_score"
    if not output.resolve().is_relative_to((win.ROOT / "outputs").resolve()):
        output = win.ROOT / "outputs" / ("p1b_unit_fixture_" + uuid.uuid4().hex)
    before = {name: file_sha256(prepared.root / name / (name + ".sqlite")) for name in ("demo", "large")}
    process = win.run_isolated(bundle, prepared.root, output, manifest=prepared.frozen / "manifest.json",
                               sql_timeout=2, worker_timeout=10, outer_timeout=90)
    assert process["exit_code"] == 0 and not process["timed_out"]
    summary = read_json(output / "summary.json")
    assert summary["planned_cases"] == 10 and len(summary["rows"]) == 10
    assert summary["correct"] == 8 and summary["official_ex"] == .8  # Fixture control only.
    assert summary["official_commit"] == OFFICIAL_COMMIT and not summary["scoring_logic_modified"]
    assert before == {name: file_sha256(prepared.root / name / (name + ".sqlite")) for name in before}
