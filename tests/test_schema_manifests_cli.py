"""Offline metadata selection and audited schema_only CLI with an injected SDK."""
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

import run_eval
from bird_starter.experiment_sets import make_dev_manifest, select_manifest_cases
from bird_starter.model import Model


def test_metadata_stratification_never_reads_gold(tmp_path):
    class Record(dict):
        def __getitem__(self, key):
            assert key != "SQL"
            return super().__getitem__(key)
    records = [Record(case_id=n, db_id=db, difficulty=d, SQL="DO_NOT_READ")
               for n, (db, d) in enumerate([(db, d) for db in ("a", "b") for d in ("simple", "moderate", "challenging", "simple")])]
    manifest = make_dev_manifest(records, "fixture-sha", "fixture-revision")
    assert manifest["case_count"] == 6 and manifest["database_count"] == 2
    assert manifest["difficulty_counts"] == {"simple": 2, "moderate": 2, "challenging": 2}
    assert manifest == make_dev_manifest(list(reversed(records)), "fixture-sha", "fixture-revision")
    assert "DO_NOT_READ" not in json.dumps(manifest)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert [r["case_id"] for r in select_manifest_cases(records, path, "fixture-sha")] == [0, 1, 2, 4, 5, 6]
    with pytest.raises(ValueError):
        select_manifest_cases(records, path, "wrong-fingerprint")


def test_missing_difficulty_strata_fill_is_stable():
    records = [{"case_id": n, "db_id": "a", "difficulty": "simple"} for n in (7, 3, 5, 1)]
    assert [r["case_id"] for r in make_dev_manifest(records, "fixture", "fixture")["cases"]] == [1, 3, 5]


@pytest.mark.parametrize("damage", ["duplicate", "db", "difficulty", "gold"])
def test_manifest_identity_and_metadata_rejection(tmp_path, damage):
    records = [{"case_id": n, "db_id": "demo", "difficulty": "simple"} for n in (1, 2, 3)]
    manifest = make_dev_manifest(records, "fixture", "fixture")
    if damage == "duplicate":
        manifest["cases"].append(manifest["cases"][0])
    elif damage == "db":
        manifest["cases"][0]["db_id"] = "other"
    elif damage == "difficulty":
        manifest["cases"][0]["difficulty"] = "other"
    else:
        manifest["cases"][0]["SQL"] = "must not accept"
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        select_manifest_cases(records, path, "fixture")


@pytest.mark.parametrize("enabled,calls_expected", [("on", 2), ("off", 1)])
def test_schema_cli_shared_audit_and_no_gold(tmp_path, tiny_db, monkeypatch, enabled, calls_expected):
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"question_id": 7, "db_id": "demo", "question": "fixture amount", "evidence": "clue",
                                 "SQL": "GOLD_ONLY_CLI_SENTINEL"}]))
    calls = []
    answers = iter(([json.dumps({"tables": [{"name": "orders", "columns": ["amount"]}]})] if enabled == "on" else []) +
                   ["SELECT SUM(amount) FROM orders"])
    def create(**kwargs):
        calls.append(kwargs)
        return NS(choices=[NS(message=NS(content=next(answers)), finish_reason="stop")])
    monkeypatch.setattr(run_eval, "Model", lambda **kw: Model(**kw, client=NS(chat=NS(completions=NS(create=create)))))
    out = tmp_path / "schema.jsonl"
    summary = run_eval.main(["--mode", "schema_only", "--schema-linking", enabled, "--cases", str(cases),
        "--db-root", str(tiny_db[0]), "--model", "fixture", "--request-retries", "0", "--max-model-calls", "2",
        "--allow-api", "--no-proxy", "--out", str(out), "--max-repairs", "9"])
    row = json.loads(out.read_text())
    config = json.loads(out.with_suffix(".config.json").read_text())
    assert config["max_repairs"] == 0 and config["schema_linking"] == (enabled == "on")
    assert summary["model_calls"] == len(calls) == summary["request_audit"]["prepared_attempts"] == calls_expected
    assert len(row["request_attempt_ids"]) == calls_expected and row["execution_ok"]
    assert row["schema_linking"]["model_calls"] == calls_expected - 1
    assert "GOLD_ONLY_CLI_SENTINEL" not in json.dumps(calls) + out.read_text()
    assert "repair" not in [entry["node"] for entry in row["trace"]]


def test_schema_cli_dry_run_no_model_and_manifest_order(tmp_path, tiny_db, monkeypatch):
    path = tmp_path / "fixture.json"
    cases = [{"question_id": n, "db_id": "demo", "question": "fixture", "evidence": "", "SQL": "fixture gold",
              "difficulty": "simple"} for n in (1, 2)]
    path.write_text(json.dumps(cases))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                   "cases": [{"case_id": n, "db_id": "demo", "difficulty": "simple"} for n in (2, 1)]}))
    monkeypatch.setattr(run_eval, "Model", lambda **kw: pytest.fail("Dry-run created a model"))
    out = tmp_path / "dry.jsonl"
    summary = run_eval.main(["--mode", "schema_only", "--dry-run", "--request-retries", "0",
                            "--cases", str(path), "--db-root", str(tiny_db[0]), "--case-manifest", str(manifest),
                            "--out", str(out)])
    assert summary["preflight_successes"] == 2 and summary["model_calls"] == 0
    assert [json.loads(row)["case_id"] for row in out.read_text().splitlines()] == [2, 1]


def test_schema_cli_requires_authorization_and_zero_retry(tmp_path):
    out = tmp_path / "no-run.jsonl"
    with pytest.raises(SystemExit) as exc:
        run_eval.main(["--mode", "schema_only", "--request-retries", "0", "--out", str(out)])
    assert exc.value.code == 2 and not out.exists()
    with pytest.raises(SystemExit) as exc:
        run_eval.main(["--mode", "schema_only", "--request-retries", "1", "--dry-run", "--out", str(out)])
    assert exc.value.code == 2 and not out.exists()


def test_reused_graph_resets_fallback_trace_and_case_accounting(tmp_path, tiny_db, monkeypatch):
    path = tmp_path / "two-fixture-cases.json"
    path.write_text(json.dumps([{"question_id": n, "db_id": "demo", "question": f"fixture question {n}",
                                 "evidence": "", "SQL": "GOLD_ONLY_SENTINEL"} for n in (1, 2)]))
    answers = iter(["invalid JSON", "SELECT 1", json.dumps({"tables": [{"name": "orders", "columns": ["amount"]}]}),
                    "SELECT SUM(amount) FROM orders"])
    prompts = []
    def create(**params):
        prompts.append(params)
        return NS(choices=[NS(message=NS(content=next(answers)), finish_reason="stop")])
    monkeypatch.setattr(run_eval, "Model", lambda **kw: Model(**kw, client=NS(chat=NS(completions=NS(create=create)))))
    out = tmp_path / "two.jsonl"
    summary = run_eval.main(["--mode", "schema_only", "--cases", str(path), "--db-root", str(tiny_db[0]),
                            "--model", "fixture", "--request-retries", "0", "--max-model-calls", "4",
                            "--no-proxy", "--allow-api", "--out", str(out)])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 2 and summary["model_calls"] == 4
    assert rows[0]["schema_linking"]["fallback"] and not rows[1]["schema_linking"]["fallback"]
    assert all(len(r["trace"]) == 4 and r["model_calls"] == 2 for r in rows)
    assert rows[1]["schema_linking"]["selection_valid"] is True
    assert all("fixture question 1" not in str(p) for p in prompts[2:])
    assert [a["case_id"] for a in summary["request_audit"]["attempts"]] == [1, 1, 2, 2]
