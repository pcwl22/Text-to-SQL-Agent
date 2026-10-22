"""Offline CLI checks; tiny fixtures are explicitly rejected as real benchmark data."""
import hashlib
import json

import pytest

from scripts.evaluate_schema_linking import main as evaluate_main
from scripts.prepare_schema_dev_set import main as prepare_main


def test_preparation_rejects_synthetic_benchmark(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text("[]")
    with pytest.raises(SystemExit) as exc:
        prepare_main(["--cases", str(path), "--baseline-config", str(tmp_path / "absent.json"),
                      "--out-dir", str(tmp_path / "no-output")])
    assert exc.value.code == 2 and not (tmp_path / "no-output").exists()


@pytest.mark.parametrize("missing", [False, True])
def test_evaluator_cli_gold_is_private_and_missing_cases_explicit(tmp_path, tiny_db, missing):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"question_id": i, "db_id": "demo", "difficulty": "simple", "question": "fixture",
                                  "evidence": "", "SQL": "SELECT amount FROM orders WHERE 'GOLD_PRIVATE_LITERAL' != ''"}
                                 for i in (1, 2, 3)]))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dataset_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
            "cases": [{"case_id": i, "db_id": "demo", "difficulty": "simple"} for i in (1, 2, 3)]}))
    selected = tmp_path / "selections.jsonl"
    selected.write_text("\n".join(json.dumps({"case_id": i, "db_id": "demo", "offline_mock": True,
        "schema_selection": {"tables": {"orders": ["amount"]}},
        "schema_linking": {"selection_valid": True, "model_calls": 1}}) for i in ((1, 2) if missing else (1, 2, 3))))
    out = tmp_path / "evaluation.json"
    argv = ["--cases", str(cases), "--db-root", str(tiny_db[0]), "--manifest", str(manifest),
            "--selections", str(selected), "--out", str(out)]
    assert evaluate_main(argv) == int(missing)
    report = json.loads(out.read_text())
    assert report["planned_cases"] == 3 and report["missing_selection_case_ids"] == ([3] if missing else [])
    assert report["summary"]["column_recall_micro"] == 1
    assert report["all_selections_offline_mock"] and report["model_api_calls"] == 0
    assert "GOLD_PRIVATE_LITERAL" not in out.read_text()
    original = out.read_bytes()
    with pytest.raises(SystemExit) as exc:
        evaluate_main(argv)
    assert exc.value.code == 2 and out.read_bytes() == original
